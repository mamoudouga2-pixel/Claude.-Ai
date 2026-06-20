'use strict';

const express = require('express');
const cors = require('cors');
const config = require('./config');
const { performSearch } = require('./search');

const app = express();
app.use(cors({ origin: config.ALLOWED_ORIGIN }));
app.use(express.json({ limit: '8mb' })); // generous limit so base64 images fit

/* ------------------------------------------------------------------ */
/* Tiny in-memory response cache.                                      */
/* Works well on always-on hosts (Render / Railway / Fly.io / a VPS).  */
/* On serverless platforms each invocation may start a fresh process,  */
/* so this becomes a harmless no-op there rather than a real cache —   */
/* don't rely on it for hit rates on Vercel-style hosting.             */
/* ------------------------------------------------------------------ */

const cache = new Map();
function cacheGet(key) {
  const hit = cache.get(key);
  if (!hit) return null;
  if (Date.now() - hit.time > config.CACHE_TTL_MS) {
    cache.delete(key);
    return null;
  }
  return hit.value;
}
function cacheSet(key, value) {
  cache.set(key, { value, time: Date.now() });
}

/* ------------------------------------------------------------------ */
/* Groq — fast "thinking" step: turn a question into search queries.   */
/* ------------------------------------------------------------------ */

async function planSearchQueries(question) {
  if (!config.GROQ_API_KEY) return [question];
  try {
    const res = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${config.GROQ_API_KEY}`,
      },
      body: JSON.stringify({
        model: config.GROQ_MODEL,
        temperature: 0.2,
        messages: [
          {
            role: 'system',
            content:
              'Turn the user question into 1-3 short, effective web search queries. ' +
              'Reply with ONLY a JSON array of strings — no markdown, no commentary. ' +
              'Example: ["query one","query two"]',
          },
          { role: 'user', content: question },
        ],
      }),
    });
    if (!res.ok) return [question];
    const data = await res.json();
    const text = data.choices?.[0]?.message?.content || '';
    const start = text.indexOf('[');
    const end = text.lastIndexOf(']');
    if (start === -1 || end === -1) return [question];
    const queries = JSON.parse(text.slice(start, end + 1));
    return Array.isArray(queries) && queries.length ? queries.slice(0, 3) : [question];
  } catch (err) {
    console.error('[groq] query planning failed:', err.message);
    return [question];
  }
}

/* ------------------------------------------------------------------ */
/* Gemini — final answer synthesis (text) and vision (image) calls.    */
/* ------------------------------------------------------------------ */

async function callGemini(parts) {
  const url =
    `https://generativelanguage.googleapis.com/v1beta/models/${config.GEMINI_MODEL}:generateContent` +
    `?key=${config.GEMINI_API_KEY}`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ contents: [{ parts }] }),
  });
  if (!res.ok) {
    const errText = await res.text().catch(() => '');
    throw new Error(`Gemini request failed (${res.status}): ${errText.slice(0, 200)}`);
  }
  const data = await res.json();
  return (data.candidates?.[0]?.content?.parts || []).map((p) => p.text || '').join('\n');
}

function parseJsonAnswer(text) {
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start === -1 || end === -1) throw new Error('No JSON object found in AI response');
  return JSON.parse(text.slice(start, end + 1));
}

const ANSWER_SHAPE_INSTRUCTIONS =
  'Reply with ONLY a single valid JSON object — no markdown fences, no commentary. Shape: ' +
  '{"shortAnswer": string (max 18 words), "detailedAnswer": string (max 70 words), ' +
  '"bullets": array of short strings or null, ' +
  '"table": {"headers": array, "rows": array of arrays} or null, ' +
  '"example": string (max 25 words) or null}';

const LANGUAGE_LINE = {
  bn: 'সব উত্তর বাংলায় লিখো।',
  en: 'Answer in English.',
};

async function synthesizeFromSources(question, sources, language) {
  const langLine = LANGUAGE_LINE[language] || LANGUAGE_LINE.en;
  const context = sources
    .map((s, i) => `[${i + 1}] ${s.title} (${s.domain})\n${s.content}`)
    .join('\n\n')
    .slice(0, 12000);

  const prompt = sources.length
    ? `Question: ${question}\n\nUse the following sources to answer, in your own words (do not quote them verbatim):\n\n${context}\n\n${langLine}\n${ANSWER_SHAPE_INSTRUCTIONS}`
    : `Question: ${question}\n\nNo search results were available — answer from your own knowledge.\n\n${langLine}\n${ANSWER_SHAPE_INSTRUCTIONS}`;

  const text = await callGemini([{ text: prompt }]);
  return parseJsonAnswer(text);
}

async function answerFromImage(question, image, language) {
  const langLine = LANGUAGE_LINE[language] || LANGUAGE_LINE.en;
  const prompt =
    (question ? `Question: ${question}\n\n` : 'Describe what is in this image in detail.\n\n') +
    `${langLine}\n${ANSWER_SHAPE_INSTRUCTIONS}`;
  const parts = [
    { inline_data: { mime_type: image.mediaType, data: image.data } },
    { text: prompt },
  ];
  const text = await callGemini(parts);
  return parseJsonAnswer(text);
}

/* ------------------------------------------------------------------ */
/* Routes                                                              */
/* ------------------------------------------------------------------ */

app.get('/api/health', (_req, res) => res.json({ ok: true }));

app.post('/api/search', async (req, res) => {
  const { question, image, language } = req.body || {};
  if (!question && !image) {
    return res.status(400).json({ error: 'question or image is required' });
  }
  if (!config.GEMINI_API_KEY) {
    return res.status(500).json({ error: 'GEMINI_API_KEY is not configured on the server' });
  }

  const cacheKey = JSON.stringify({ question, hasImage: !!image, language });
  const cached = cacheGet(cacheKey);
  if (cached) return res.json(cached);

  try {
    let answer;
    let sources = [];

    if (image) {
      answer = await answerFromImage(question, image, language);
    } else {
      const queries = await planSearchQueries(question);
      sources = await performSearch(queries);
      answer = await synthesizeFromSources(question, sources, language);
    }

    const payload = {
      ...answer,
      sources: sources.map(({ title, domain, url, description, logo }) => ({
        title,
        domain,
        url,
        description,
        logo,
      })),
    };

    cacheSet(cacheKey, payload);
    res.json(payload);
  } catch (err) {
    console.error('[search] request failed:', err);
    res.status(500).json({ error: 'Failed to generate an answer. Please try again.' });
  }
});

/* ------------------------------------------------------------------ */
/* Start the server. On Vercel the exported app is wrapped by their    */
/* Node runtime instead, so app.listen is skipped there.               */
/* ------------------------------------------------------------------ */

if (process.env.VERCEL !== '1') {
  app.listen(config.PORT, () => {
    console.log(`Khoj backend listening on port ${config.PORT}`);
  });
}

module.exports = app;
