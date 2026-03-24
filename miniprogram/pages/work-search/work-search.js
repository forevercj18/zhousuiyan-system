const api = require('../../utils/api');

Page({
  data: {
    keyword: '',
    loading: false,
    searched: false,
    reservationResults: [],
    orderResults: []
  },

  onLoad(options) {
    if (options.keyword) {
      this.setData({ keyword: options.keyword });
      this.doSearch();
    }
  },

  onPullDownRefresh() {
    if (!this.data.keyword.trim()) {
      wx.stopPullDownRefresh();
      return;
    }
    this.doSearch(true);
  },

  onKeywordInput(e) {
    this.setData({ keyword: e.detail.value });
  },

  clearKeyword() {
    this.setData({
      keyword: '',
      searched: false,
      reservationResults: [],
      orderResults: []
    });
  },

  onSearch() {
    this.doSearch();
  },

  doSearch(fromPullDown = false) {
    const keyword = (this.data.keyword || '').trim();
    if (!keyword) {
      wx.showToast({ title: '请输入关键词', icon: 'none' });
      if (fromPullDown) wx.stopPullDownRefresh();
      return;
    }
    this.setData({ loading: true });
    Promise.all([
      api.request(`/api/mp/staff/reservations/?keyword=${encodeURIComponent(keyword)}`, { needLogin: true }),
      api.request(`/api/mp/staff/orders/?keyword=${encodeURIComponent(keyword)}`, { needLogin: true })
    ]).then(([reservationData, orderData]) => {
      this.setData({
        loading: false,
        searched: true,
        reservationResults: reservationData.results || [],
        orderResults: orderData.results || []
      });
      if (fromPullDown) wx.stopPullDownRefresh();
    }).catch(err => {
      this.setData({ loading: false });
      if (fromPullDown) wx.stopPullDownRefresh();
      wx.showToast({ title: err.error || '搜索失败', icon: 'none' });
    });
  },

  goReservationDetail(e) {
    const id = e.currentTarget.dataset.id;
    if (!id) return;
    wx.navigateTo({ url: `/pages/work-reservation-detail/work-reservation-detail?id=${id}` });
  },

  goOrderDetail(e) {
    const id = e.currentTarget.dataset.id;
    const action = e.currentTarget.dataset.action || '';
    if (!id) return;
    const suffix = action ? `?id=${id}&action=${action}` : `?id=${id}`;
    wx.navigateTo({ url: `/pages/work-order-detail/work-order-detail${suffix}` });
  },

  callCustomer(e) {
    const phone = e.currentTarget.dataset.phone || '';
    if (!phone) {
      wx.showToast({ title: '暂无手机号', icon: 'none' });
      return;
    }
    wx.makePhoneCall({
      phoneNumber: phone,
      fail: () => wx.showToast({ title: '拨号失败', icon: 'none' })
    });
  },

  copyWechat(e) {
    const wechat = e.currentTarget.dataset.wechat || '';
    if (!wechat) return;
    wx.setClipboardData({
      data: wechat,
      success: () => wx.showToast({ title: '微信号已复制', icon: 'none' })
    });
  },

  copyOrderNo(e) {
    const orderNo = e.currentTarget.dataset.orderNo || '';
    if (!orderNo) return;
    wx.setClipboardData({
      data: orderNo,
      success: () => wx.showToast({ title: '订单号已复制', icon: 'none' })
    });
  }
});
