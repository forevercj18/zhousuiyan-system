const api = require('../../utils/api');
const app = getApp();

Page({
  data: {
    skus: [],
    keyword: '',
    loading: true,
    isEmpty: false,
    staffProfile: null,
    preferredMode: 'customer',
    workSummary: null
  },

  onLoad() {
    this.loadSkus();
  },

  onShow() {
    this.loadStaffProfile();
    this.setData({ preferredMode: api.getPreferredMode() });
  },

  onPullDownRefresh() {
    this.loadSkus().then(() => {
      wx.stopPullDownRefresh();
    });
  },

  /** 加载产品列表 */
  loadSkus() {
    this.setData({ loading: true });
    let path = '/api/mp/skus/';
    const params = [];
    if (this.data.keyword) {
      params.push('keyword=' + encodeURIComponent(this.data.keyword));
    }
    if (params.length) {
      path += '?' + params.join('&');
    }

    return api.request(path).then(data => {
      this.setData({
        skus: data.results || [],
        loading: false,
        isEmpty: !(data.results && data.results.length)
      });
    }).catch(err => {
      console.error('加载产品失败', err);
      this.setData({ loading: false, isEmpty: true });
      wx.showToast({ title: err.error || '加载失败', icon: 'none' });
    });
  },

  /** 搜索输入 */
  onSearchInput(e) {
    this.setData({ keyword: e.detail.value });
  },

  /** 搜索确认 */
  onSearch() {
    this.loadSkus();
  },

  /** 清除搜索 */
  onClearSearch() {
    this.setData({ keyword: '' });
    this.loadSkus();
  },

  /** 跳转产品详情 */
  goDetail(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/detail/detail?id=' + id });
  },

  loadStaffProfile() {
    if (!app.globalData.isLogin) {
      this.setData({ staffProfile: null, preferredMode: 'customer', workSummary: null });
      return;
    }
    api.getStaffProfile().then(data => {
      if (!(data.staff || null)) {
        this.setData({
          staffProfile: null,
          preferredMode: api.getPreferredMode(),
          workSummary: null
        });
        return null;
      }
      this.setData({
        staffProfile: data.staff || null,
        preferredMode: api.getPreferredMode()
      });
      return api.request('/api/mp/staff/dashboard/', { needLogin: true });
    }).then(data => {
      if (!data) return;
      const shortcuts = data.shortcuts || [];
      const urgentKeys = ['overdue_contact', 'shipment_overdue', 'balance_pending'];
      const urgentItems = shortcuts.filter(item => urgentKeys.includes(item.key) && Number(item.count || 0) > 0);
      const total = urgentItems.reduce((sum, item) => sum + Number(item.count || 0), 0);
      this.setData({
        workSummary: total ? {
          total,
          labels: urgentItems.map(item => `${item.label}${item.count}`)
        } : null
      });
    }).catch(() => {
      this.setData({ staffProfile: null, workSummary: null });
    });
  },

  goWorkHome() {
    api.setPreferredMode('work');
    wx.navigateTo({ url: '/pages/work-home/work-home' });
  },

  goWorkBind() {
    wx.navigateTo({ url: '/pages/work-bind/work-bind' });
  },

  stayCustomerMode() {
    api.setPreferredMode('customer');
    wx.showToast({ title: '当前为客户模式', icon: 'none' });
  }
});
