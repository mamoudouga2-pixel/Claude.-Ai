'use strict';

/**
 * Khoj AI Search — central configuration.
 *
 * Every value here comes from an environment variable. Nothing is
 * hardcoded. Locally, copy .env.example to .env and fill it in.
 * On a host (Vercel / Render / Railway / Fly.io / etc.) set these
 * in that provider's "Environment Variables" panel instead — do not
 * commit a real .env file.
 */

// dotenv is only useful for local development. On real hosting providers
// the platform injects env vars directly, so this is a harmless no-op there.
try {
  require('dotenv').config();
} catch {
  /* dotenv not installed / not needed in this environment — fine. */
}

const config = {
  PORT: parseInt(process.env.PORT || '3000', 10),
  ALLOWED_ORIGIN: process.env.ALLOWED_ORIGIN || '*',

  // --- AI providers -------------------------------------------------
  GEMINI_API_KEY: process.env.GEMINI_API_KEY || '',
  // Verify the current model name in Google's docs before deploying —
  // model names change as new versions ship.
  // https://ai.google.dev/gemini-api/docs/models
  GEMINI_MODEL: process.env.GEMINI_MODEL || 'gemini-2.0-flash',

  GROQ_API_KEY: process.env.GROQ_API_KEY || '',
  // Verify against Groq's current model list before deploying.
  // https://console.groq.com/docs/models
  GROQ_MODEL: process.env.GROQ_MODEL || 'llama-3.3-70b-versatile',

  // --- Search engines -------------------------------------------------
  // A SearXNG instance with JSON output enabled (`format=json` in its
  // settings). Many public instances disable this to deter scraping —
  // host your own, or pick one from https://searx.space that allows it.
  // Leave blank to skip SearXNG and rely on DuckDuckGo only.
  SEARXNG_URL: process.env.SEARXNG_URL || '',

  DUCKDUCKGO_URL: process.env.DUCKDUCKGO_URL || 'https://html.duckduckgo.com/html/',

  // --- Tuning -------------------------------------------------
  MAX_SOURCES: parseInt(process.env.MAX_SOURCES || '5', 10),
  FETCH_TIMEOUT_MS: parseInt(process.env.FETCH_TIMEOUT_MS || '8000', 10),
  CONTENT_CHAR_LIMIT: parseInt(process.env.CONTENT_CHAR_LIMIT || '2500', 10),
  CACHE_TTL_MS: parseInt(process.env.CACHE_TTL_MINUTES || '10', 10) * 60 * 1000,
};

module.exports = config;
