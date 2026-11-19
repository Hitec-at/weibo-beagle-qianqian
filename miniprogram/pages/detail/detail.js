const app = getApp();

Page({
  data: {
    id: "",
    post: null,
    loading: true,
    error: "",
    videoUrl: "",
    videoError: ""
  },

  onLoad(options) {
    this.setData({ id: options.id || "" });
    this.load();
  },

  async load() {
    this.setData({ loading: true, error: "" });
    try {
      const post = await app.getClient().requestData(
        "/posts/" + encodeURIComponent(this.data.id)
      );
      this.setData({ post });
      if (post.video) this.loadVideo(post.video.source);
    } catch (error) {
      this.setData({ error: error.message || "加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadVideo(source) {
    try {
      const data = await app.getClient().requestData(
        "/posts/" + encodeURIComponent(this.data.id) + "/video",
        { source: source || 0 }
      );
      this.setData({ videoUrl: data.url, videoError: "" });
    } catch (error) {
      this.setData({
        videoUrl: "",
        videoError: (error.message || "视频暂不可用") + "，可复制原微博链接查看"
      });
    }
  },

  previewImage(event) {
    const post = this.data.post;
    const all = post.pics.concat((post.source && post.source.pics) || []);
    wx.previewImage({
      current: event.currentTarget.dataset.url,
      urls: all.map(item => item.url || item.thumb)
    });
  },

  copyOriginal() {
    if (!this.data.post || !this.data.post.original_url) return;
    wx.setClipboardData({ data: this.data.post.original_url });
  },

  copyVideoPage() {
    const post = this.data.post;
    const url =
      (post && post.video && post.video.page_url) ||
      (post && post.source && post.source.video && post.source.video.page_url);
    if (url) wx.setClipboardData({ data: url });
  }
});
