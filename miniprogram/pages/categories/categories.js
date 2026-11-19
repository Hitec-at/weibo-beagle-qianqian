const app = getApp();

Page({
  data: {
    loading: true,
    error: "",
    feeds: [],
    behavior: [],
    fanFiction: []
  },

  onLoad() {
    this.load();
  },

  async load() {
    this.setData({ loading: true, error: "" });
    try {
      const client = app.getClient();
      const [bootstrap, behavior, fanFiction] = await Promise.all([
        client.requestData("/bootstrap"),
        client.requestData("/memes", { kind: "behavior" }),
        client.requestData("/memes", { kind: "fan_fiction" })
      ]);
      this.setData({
        feeds: bootstrap.feeds,
        behavior: behavior.items,
        fanFiction: fanFiction.items
      });
    } catch (error) {
      this.setData({ error: error.message || "加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  openFeed(event) {
    const item = event.currentTarget.dataset.item;
    wx.navigateTo({
      url:
        "/pages/feed/feed?feed=" +
        encodeURIComponent(item.key) +
        "&title=" +
        encodeURIComponent(item.title)
    });
  },

  openMeme(event) {
    const item = event.currentTarget.dataset.item;
    wx.navigateTo({
      url:
        "/pages/feed/feed?meme=" +
        encodeURIComponent(item.slug) +
        "&title=" +
        encodeURIComponent(item.name)
    });
  }
});
