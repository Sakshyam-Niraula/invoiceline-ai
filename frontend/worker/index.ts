/** Cloudflare Worker entry point that delegates requests to vinext. */
import handler from "vinext/server/fetch-handler";

type FetchHandler = typeof handler.fetch;

export default {
  fetch(
    request: Parameters<FetchHandler>[0],
    env: Parameters<FetchHandler>[1],
    ctx: Parameters<FetchHandler>[2],
  ): ReturnType<FetchHandler> {
    return handler.fetch(request, env, ctx);
  },
};
