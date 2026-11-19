const app = getApp();

Page({
  data: {
    feed: "home",
    meme: "",
    title: "微博列表",
    grid: false,
    sort: "hot",
    sorts: [
      { value: "hot", label: "热门" },
      { value: "latest", label: "最新" },
      { value: "earliest", label: "最早" }
    ],
    posts: [],
    page: 1,
    hasMore: false,
    loading: true,
    error: ""
  },

  onLoad(options) {
    this.setData({
      feed: options.feed || "home",
      meme: options.meme || "",
      title: options.title ? decodeURIComponent(options.title) : "微博列表",
      grid: options.grid === "1"
    });
    wx.setNavigationBarTitle({ title: this.data.title });
    this.load(1, false);
  },

  onReachBottom() {
    if (!this.data.hasMore || this.data.loading) return;
    this.load(this.data.page + 1, true);
  },

  onPullDownRefresh() {
    this.load(1, false).finally(() => wx.stopPullDownRefresh());
  },

  async load(page, append) {
    this.setData({ loading: true, error: "" });
    try {
      const data = await app.getClient().requestData("/posts", {
        feed: this.data.feed,
        meme: this.data.meme,
        page,
        page_size: 20,
        sort: this.data.sort
      });
      this.setData({
        posts: append ? this.data.posts.concat(data.items) : data.items,
        page: data.page,
        hasMore: data.has_more
      });
    } catch (error) {
      this.setData({ error: error.message || "加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  changeSort(event) {
    const sort = event.currentTarget.dataset.sort;
    if (sort === this.data.sort) return;
    this.setData({ sort });
    this.load(1, false);
  },

  openDetail(event) {
    wx.navigateTo({
      url: "/pages/detail/detail?id=" + encodeURIComponent(event.currentTarget.dataset.id)
    });
  }
});
