import {connect} from "cloudflare:sockets";
import {scheduled, serve} from "./core.mjs";

export default {
  async scheduled(controller, env) {
    console.log(JSON.stringify(await scheduled(controller, env, connect)));
  },
  async fetch(request, env) {
    return serve(request, env);
  }
};
