// Cloudflare Worker: OpenAI API Proxy
// Deploy as "openai-proxy" worker
// Set environment variable PROXY_SECRET in worker settings

export default {
  async fetch(request, env) {
    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
          "Access-Control-Allow-Headers": "*",
        },
      });
    }

    // Verify proxy token
    const proxyToken = request.headers.get("x-proxy-token");
    if (!proxyToken || proxyToken !== env.PROXY_SECRET) {
      return new Response("Unauthorized", { status: 401 });
    }

    // Build target URL
    const url = new URL(request.url);
    const targetUrl = `https://api.openai.com${url.pathname}${url.search}`;

    // Forward request, removing proxy-specific headers
    const headers = new Headers(request.headers);
    headers.delete("x-proxy-token");

    const response = await fetch(targetUrl, {
      method: request.method,
      headers: headers,
      body: request.method !== "GET" ? request.body : undefined,
    });

    // Return response with CORS headers
    const responseHeaders = new Headers(response.headers);
    responseHeaders.set("Access-Control-Allow-Origin", "*");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  },
};
