import {connect} from "cloudflare:sockets";
import {scheduled, serve} from "./core.mjs";

export default {
  async scheduled(controller, env) {
    console.log(JSON.stringify({status: "started", scheduled_time: controller.scheduledTime, cron: controller.cron}));
    try {
      console.log(JSON.stringify(await scheduled(controller, env, connect)));
    } catch (error) {
      console.error(JSON.stringify({status: "failed", error: error?.message ?? String(error)}));
      throw error;
    }
  },
  async fetch(request, env) {
    return serve(request, env);
  }
};
