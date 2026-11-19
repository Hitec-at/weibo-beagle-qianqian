const app = getApp();

Page({
  data: {
    loading: true,
    error: "",
    feeds: [],
    posts: [],
    sorts: [],
    sort: "hot",
    rangeText: "",
    notice: "",
    page: 1,
    hasMore: false
  },

  onLoad() {
    this.refresh();
  },

  onPullDownRefresh() {
    this.refresh().finally(() => wx.stopPullDownRefresh());
  },

  onReachBottom() {
    if (!this.data.hasMore || this.data.loading) return;
    this.loadPosts(this.data.page + 1, true);
  },

  async refresh() {
    this.setData({ loading: true, error: "" });
    try {
      const data = await app.getClient().requestData("/bootstrap");
      const range = data.time_range;
      const rangeText = range.min_text
        ? range.min_text + " 至 " + range.max_text
        : "暂无收录微博";
      this.setData({
        feeds: data.feeds,
        sorts: data.sorts,
        sort: data.default_sort || "hot",
        rangeText,
        notice: data.notice || ""
      });
      await this.loadPosts(1, false);
    } catch (error) {
      this.setData({ error: error.message || "加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadPosts(page, append) {
    const data = await app.getClient().requestData("/posts", {
      feed: "home",
      page,
      page_size: 20,
      sort: this.data.sort
    });
    this.setData({
      posts: append ? this.data.posts.concat(data.items) : data.items,
      page: data.page,
      hasMore: data.has_more
    });
  },

  changeSort(event) {
    const sort = event.currentTarget.dataset.sort;
    if (sort === this.data.sort) return;
    this.setData({ sort });
    this.loadPosts(1, false).catch(error => {
      this.setData({ error: error.message || "加载失败" });
    });
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

  openCategories() {
    wx.navigateTo({ url: "/pages/categories/categories" });
  },

  openSearch() {
    wx.navigateTo({ url: "/pages/search/search" });
  },

  openDetail(event) {
    wx.navigateTo({
      url: "/pages/detail/detail?id=" + encodeURIComponent(event.currentTarget.dataset.id)
    });
  },

  previewImage(event) {
    const post = this.data.posts[Number(event.currentTarget.dataset.index || 0)];
    const current = event.currentTarget.dataset.url;
    if (!post) return;
    wx.previewImage({
      current,
      urls: post.pics.map(item => item.url || item.thumb)
    });
  }
});
