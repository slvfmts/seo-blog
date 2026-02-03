/**
 * Cloudflare Worker — прокси для Anthropic API
 * Обходит географические ограничения
 */

const ANTHROPIC_API = 'https://api.anthropic.com';

export default {
  async fetch(request, env) {
    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: {
          'Access-Control-Allow-Origin': '*',
          'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
          'Access-Control-Allow-Headers': 'Content-Type, x-api-key, anthropic-version',
        },
      });
    }

    // Проверка секретного токена (защита от abuse)
    const proxyToken = request.headers.get('x-proxy-token');
    if (proxyToken !== env.PROXY_SECRET) {
      return new Response(JSON.stringify({ error: 'Unauthorized' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      });
    }

    // Получаем путь из URL
    const url = new URL(request.url);
    const targetUrl = ANTHROPIC_API + url.pathname + url.search;

    // Копируем заголовки, убирая proxy-token
    const headers = new Headers(request.headers);
    headers.delete('x-proxy-token');

    // Проксируем запрос
    const response = await fetch(targetUrl, {
      method: request.method,
      headers: headers,
      body: request.method !== 'GET' ? await request.text() : null,
    });

    // Возвращаем ответ с CORS
    const responseHeaders = new Headers(response.headers);
    responseHeaders.set('Access-Control-Allow-Origin', '*');

    return new Response(response.body, {
      status: response.status,
      headers: responseHeaders,
    });
  },
};
