const app = getApp();

Page({
  data: {
    query: "",
    searched: false,
    sort: "relevance",
    sorts: [
      { value: "relevance", label: "相关度" },
      { value: "latest", label: "最新" },
      { value: "hot", label: "热门" }
    ],
    posts: [],
    page: 1,
    hasMore: false,
    loading: false,
    error: ""
  },

  onInput(event) {
    this.setData({ query: event.detail.value });
  },

  onSearch() {
    const query = this.data.query.trim();
    if (!query) {
      this.setData({ error: "请输入搜索词", posts: [], searched: false });
      return;
    }
    this.load(1, false);
  },

  changeSort(event) {
    const sort = event.currentTarget.dataset.sort;
    if (sort === this.data.sort) return;
    this.setData({ sort });
    if (this.data.searched) this.load(1, false);
  },

  onReachBottom() {
    if (!this.data.hasMore || this.data.loading) return;
    this.load(this.data.page + 1, true);
  },

  async load(page, append) {
    this.setData({ loading: true, error: "" });
    try {
      const data = await app.getClient().requestData("/search", {
        q: this.data.query.trim(),
        page,
        page_size: 20,
        sort: this.data.sort
      });
      this.setData({
        posts: append ? this.data.posts.concat(data.items) : data.items,
        page: data.page,
        hasMore: data.has_more,
        searched: true
      });
    } catch (error) {
      this.setData({ error: error.message || "搜索失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  openDetail(event) {
    wx.navigateTo({
      url: "/pages/detail/detail?id=" + encodeURIComponent(event.currentTarget.dataset.id)
    });
  }
});
