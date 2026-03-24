const api = require('../../utils/api');
const app = getApp();

Page({
  data: {
    orders: [],
    loading: true,
    isEmpty: false,
    needLogin: false
  },

  onShow() {
    // 每次显示时刷新（可能从下单页返回）
    if (app.globalData.isLogin) {
      this.loadOrders();
    } else {
      this.setData({ loading: false, needLogin: true });
    }
  },

  onPullDownRefresh() {
    if (app.globalData.isLogin) {
      this.loadOrders().then(() => wx.stopPullDownRefresh());
    } else {
      wx.stopPullDownRefresh();
    }
  },

  /** 加载我的订单 */
  loadOrders() {
    this.setData({ loading: true, needLogin: false });
    return api.request('/api/mp/my-reservations/', { needLogin: true }).then(data => {
      const orders = (data.results || []).map(item => ({
        ...item,
        statusClass: this.getStatusClass(item.status),
        journeyClass: this.getJourneyClass(item.journey_code),
        progressText: this.getProgressText(item)
      }));
      this.setData({
        orders: orders,
        loading: false,
        isEmpty: !orders.length
      });
    }).catch(err => {
      if (err.needLogin) {
        this.setData({ loading: false, needLogin: true });
      } else {
        this.setData({ loading: false, isEmpty: true });
        wx.showToast({ title: err.error || '加载失败', icon: 'none' });
      }
    });
  },

  /** 登录 */
  doLogin() {
    api.login().then(() => {
      this.loadOrders();
    }).catch(err => {
      wx.showToast({ title: err.error || '登录失败', icon: 'none' });
    });
  },

  /** 跳转订单详情 */
  goDetail(e) {
    const id = e.currentTarget.dataset.id;
    wx.navigateTo({ url: '/pages/order-detail/order-detail?id=' + id });
  },

  /** 去逛逛 */
  goHome() {
    wx.switchTab({ url: '/pages/index/index' });
  },

  /** 获取状态样式类名 */
  getStatusClass(status) {
    const map = {
      'pending_info': 'status-pending',
      'ready_to_convert': 'status-confirm',
      'converted': 'status-done',
      'cancelled': 'status-cancel',
      'refunded': 'status-cancel'
    };
    return map[status] || 'status-pending';
  },

  getJourneyClass(journeyCode) {
    const map = {
      submitted: 'journey-pending',
      awaiting_contact: 'journey-warning',
      confirming: 'journey-primary',
      awaiting_shipment: 'journey-primary',
      in_fulfillment: 'journey-success',
      completed: 'journey-success',
      closed: 'journey-closed'
    };
    return map[journeyCode] || 'journey-pending';
  },

  getProgressText(item) {
    if (item.status === 'converted') {
      return item.fulfillment_stage_label || item.journey_label;
    }
    return item.contact_status_label || item.journey_label;
  }
});
