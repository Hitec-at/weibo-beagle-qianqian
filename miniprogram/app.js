const config = require("./config");
const { createClient } = require("./utils/api");

App({
  onLaunch() {
    const client = createClient(wx, config);
    this.globalData.client = client;
    if (!wx.cloud) {
      this.globalData.startupError = "微信基础库过低，请升级到 2.23.0 以上";
      return;
    }
    const options = { traceUser: false };
    if (config.cloudEnv) options.env = config.cloudEnv;
    wx.cloud.init(options);
  },

  getClient() {
    if (this.globalData.startupError) {
      throw new Error(this.globalData.startupError);
    }
    return this.globalData.client;
  },

  globalData: {
    client: null,
    startupError: ""
  }
});
