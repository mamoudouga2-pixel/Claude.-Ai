'use strict';

const cheerio = require('cheerio');
const config = require('./config');

/* ------------------------------------------------------------------ */
/* Low-level fetch with a hard timeout                                 */
/* ------------------------------------------------------------------ */

async function fetchWithTimeout(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, {
      ...options,
      signal: controller.signal,
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; KhojBot/1.0)',
        ...options.headers,
      },
    });
  } finally {
    clearTimeout(timer);
  }
}

/* ------------------------------------------------------------------ */
/* DuckDuckGo — no official API, scrapes the no-JS HTML endpoint.      */
/* Note: DuckDuckGo's markup can change without notice; if this stops  */
/* returning results, re-check the selectors below against a fresh    */
/* response from DUCKDUCKGO_URL.                                      */
/* ------------------------------------------------------------------ */

async function searchDuckDuckGo(query) {
  try {
    const url = `${config.DUCKDUCKGO_URL}?q=${encodeURIComponent(query)}`;
    const res = await fetchWithTimeout(url);
    if (!res.ok) return [];
    const html = await res.text();
    const $ = cheerio.load(html);
    const results = [];

    $('.result').each((_, el) => {
      const titleEl = $(el).find('.result__a').first();
      const title = titleEl.text().trim();
      let href = titleEl.attr('href') || '';

      // DDG's HTML results wrap the real URL in a redirect link:
      // //duckduckgo.com/l/?uddg=<encoded-url>&...
      if (href.includes('uddg=')) {
        const qsIndex = href.indexOf('?');
        if (qsIndex !== -1) {
          const params = new URLSearchParams(href.slice(qsIndex + 1));
          const real = params.get('uddg');
          if (real) href = decodeURIComponent(real);
        }
      }
      if (href.startsWith('//')) href = 'https:' + href;

      const snippet = $(el).find('.result__snippet').text().trim();
      if (title && href.startsWith('http')) {
        results.push({ title, url: href, snippet });
      }
    });

    return results;
  } catch (err) {
    console.error('[duckduckgo] search failed:', err.message);
    return [];
  }
}

/* ------------------------------------------------------------------ */
/* SearXNG — public instance, JSON output format.                      */
/* ------------------------------------------------------------------ */

async function searchSearXNG(query) {
  if (!config.SEARXNG_URL) return [];
  try {
    const base = config.SEARXNG_URL.replace(/\/$/, '');
    const url = `${base}/search?q=${encodeURIComponent(query)}&format=json`;
    const res = await fetchWithTimeout(url);
    if (!res.ok) return [];
    const data = await res.json();
    return (data.results || [])
      .map((r) => ({ title: r.title || '', url: r.url || '', snippet: r.content || '' }))
      .filter((r) => r.title && r.url.startsWith('http'));
  } catch (err) {
    console.error('[searxng] search failed:', err.message);
    return [];
  }
}

/* ------------------------------------------------------------------ */
/* Merge multiple result lists, dedupe by domain, rank by relevance.   */
/* A result that shows up under more than one query/engine is treated  */
/* as more relevant (best match) than one that only appears once.      */
/* ------------------------------------------------------------------ */

function domainOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return null;
  }
}

function mergeAndRank(resultLists) {
  const byDomain = new Map();
  for (const list of resultLists) {
    for (const r of list) {
      const domain = domainOf(r.url);
      if (!domain) continue;
      if (!byDomain.has(domain)) {
        byDomain.set(domain, { ...r, domain, hits: 1 });
      } else {
        byDomain.get(domain).hits += 1;
      }
    }
  }
  return Array.from(byDomain.values()).sort((a, b) => b.hits - a.hits);
}

/* ------------------------------------------------------------------ */
/* Fetch a page, extract title / description / main content / logo.   */
/* This is a lightweight readability-style heuristic (no headless      */
/* browser): strip boilerplate tags, then prefer <article>/<main>,     */
/* falling back to concatenated <p> text. Good enough for summarizing  */
/* most news/blog/wiki pages; swap in @mozilla/readability + jsdom     */
/* later if you need higher extraction accuracy on harder sites.       */
/* ------------------------------------------------------------------ */

function extractMainContent($) {
  $('script, style, noscript, nav, header, footer, aside, iframe, svg, form').remove();

  for (const sel of ['article', 'main', '[role="main"]']) {
    const text = $(sel).text().replace(/\s+/g, ' ').trim();
    if (text.length > 200) return text;
  }

  const paragraphs = [];
  $('p').each((_, el) => {
    const text = $(el).text().replace(/\s+/g, ' ').trim();
    if (text.length > 40) paragraphs.push(text);
  });
  return paragraphs.join('\n');
}

function resolveLogo($, pageUrl, domain) {
  const iconHref =
    $('link[rel="icon"]').attr('href') ||
    $('link[rel="shortcut icon"]').attr('href') ||
    $('link[rel="apple-touch-icon"]').attr('href');
  if (iconHref) {
    try {
      return new URL(iconHref, pageUrl).href;
    } catch {
      /* fall through to the default below */
    }
  }
  // Google's public favicon service — no API key required, reliable fallback.
  return `https://www.google.com/s2/favicons?sz=64&domain=${domain}`;
}

async function fetchAndExtract(result) {
  try {
    const res = await fetchWithTimeout(result.url);
    if (!res.ok) return null;
    const contentType = res.headers.get('content-type') || '';
    if (!contentType.includes('text/html')) return null;

    const html = await res.text();
    const $ = cheerio.load(html);

    const title = $('title').first().text().trim() || result.title;
    const description = (
      $('meta[name="description"]').attr('content') ||
      $('meta[property="og:description"]').attr('content') ||
      result.snippet ||
      ''
    )
      .trim()
      .slice(0, 220);
    const content = extractMainContent($).slice(0, config.CONTENT_CHAR_LIMIT);
    const logo = resolveLogo($, result.url, result.domain);

    return { title, domain: result.domain, url: result.url, description, logo, content };
  } catch (err) {
    console.error(`[fetch] failed for ${result.url}:`, err.message);
    return null;
  }
}

/* ------------------------------------------------------------------ */
/* Public entry point: queries in, ranked + enriched sources out.      */
/* ------------------------------------------------------------------ */

async function performSearch(queries) {
  const queryList = (queries || []).filter(Boolean);
  if (queryList.length === 0) return [];

  const [ddgLists, searxLists] = await Promise.all([
    Promise.all(queryList.map(searchDuckDuckGo)),
    Promise.all(queryList.map(searchSearXNG)),
  ]);

  const ranked = mergeAndRank([...ddgLists, ...searxLists]);
  const top = ranked.slice(0, config.MAX_SOURCES);
  const enriched = await Promise.all(top.map(fetchAndExtract));
  return enriched.filter(Boolean);
}

module.exports = { performSearch };
