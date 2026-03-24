const api = require('../../utils/api');

Page({
  data: {
    order: null,
    loading: true
  },

  getStatusClass(status) {
    const map = {
      'pending_info': 'bg-pending',
      'ready_to_convert': 'bg-confirm',
      'converted': 'bg-done',
      'cancelled': 'bg-cancel',
      'refunded': 'bg-cancel'
    };
    return map[status] || 'bg-pending';
  },

  getStepClass(status) {
    const map = {
      done: 'step-done',
      current: 'step-current',
      pending: 'step-pending'
    };
    return map[status] || 'step-pending';
  },

  onLoad(options) {
    if (options.id) {
      this.loadDetail(options.id);
    }
  },

  loadDetail(id) {
    this.setData({ loading: true });
    api.request('/api/mp/my-reservations/' + id + '/', { needLogin: true }).then(data => {
      const steps = (data.steps || []).map(step => ({
        ...step,
        stepClass: this.getStepClass(step.status)
      }));
      this.setData({
        order: {
          ...data,
          statusClass: this.getStatusClass(data.status),
          steps
        },
        loading: false
      });
    }).catch(err => {
      this.setData({ loading: false });
      wx.showToast({ title: err.error || '加载失败', icon: 'none' });
    });
  },

  /** 复制订单号 */
  copyOrderNo() {
    wx.setClipboardData({
      data: this.data.order.reservation_no,
      success() {
        wx.showToast({ title: '已复制', icon: 'success' });
      }
    });
  }
});
