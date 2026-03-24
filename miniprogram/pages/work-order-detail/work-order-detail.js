const api = require('../../utils/api');

Page({
  data: {
    loading: true,
    item: null,
    targetAction: '',
    shipTracking: '',
    returnTracking: '',
    balancePaid: '',
    balanceNotes: '',
    returnServiceFee: '45',
    returnServicePaymentReference: '',
    actionLoading: false,
    returnServiceTypeOptions: [
      { label: '无需包回邮', value: 'none' },
      { label: '客户自寄回', value: 'customer_self_return' },
      { label: '购买包回邮', value: 'platform_return_included' }
    ],
    paymentStatusOptions: [
      { label: '未收款', value: 'unpaid' },
      { label: '已收款', value: 'paid' },
      { label: '已退款', value: 'refunded' }
    ],
    paymentChannelOptions: [
      { label: '微信', value: 'wechat' },
      { label: '闲鱼', value: 'xianyu' },
      { label: '小红书', value: 'xiaohongshu' },
      { label: '线下', value: 'offline' }
    ],
    pickupStatusOptions: [
      { label: '待安排取件', value: 'pending_schedule' },
      { label: '已预约', value: 'scheduled' },
      { label: '已取件', value: 'picked_up' },
      { label: '已完成', value: 'completed' },
      { label: '已取消', value: 'cancelled' }
    ],
    returnServiceTypeIndex: 0,
    paymentStatusIndex: 0,
    paymentChannelIndex: 0,
    pickupStatusIndex: 0
  },

  onLoad(options) {
    this.setData({ targetAction: options.action || '' });
    if (options.id) this.loadDetail(options.id);
  },

  loadDetail(id) {
    this.setData({ loading: true });
    api.request(`/api/mp/staff/orders/${id}/`, { needLogin: true }).then(data => {
      const returnServiceTypeIndex = this.findOptionIndex(this.data.returnServiceTypeOptions, data.return_service_type || 'none');
      const paymentStatusIndex = this.findOptionIndex(this.data.paymentStatusOptions, data.return_service_payment_status || 'unpaid');
      const paymentChannelIndex = this.findOptionIndex(this.data.paymentChannelOptions, data.return_service_payment_channel || 'wechat');
      const pickupStatusIndex = this.findOptionIndex(this.data.pickupStatusOptions, data.return_pickup_status || 'pending_schedule');
      this.setData({
        item: data,
        loading: false,
        shipTracking: data.ship_tracking || '',
        returnTracking: data.return_tracking || '',
        returnServiceFee: data.return_service_fee || '45',
        returnServicePaymentReference: data.return_service_payment_reference || '',
        returnServiceTypeIndex,
        paymentStatusIndex,
        paymentChannelIndex,
        pickupStatusIndex
      });
      this.scrollToActionSection();
    }).catch(err => {
      this.setData({ loading: false });
      wx.showToast({ title: err.error || '加载失败', icon: 'none' });
    });
  },

  scrollToActionSection() {
    const action = this.data.targetAction;
    const selectorMap = {
      deliver: '#deliver-section',
      balance: '#balance-section',
      return_service: '#return-service-section',
      return: '#return-section'
    };
    const selector = selectorMap[action];
    if (!selector) return;
    setTimeout(() => {
      wx.pageScrollTo({
        selector,
        duration: 250
      });
    }, 80);
  },

  findOptionIndex(options, value) {
    const index = options.findIndex(item => item.value === value);
    return index >= 0 ? index : 0;
  },

  onShipTrackingInput(e) {
    this.setData({ shipTracking: e.detail.value });
  },

  onReturnTrackingInput(e) {
    this.setData({ returnTracking: e.detail.value });
  },

  onBalancePaidInput(e) {
    this.setData({ balancePaid: e.detail.value });
  },

  onBalanceNotesInput(e) {
    this.setData({ balanceNotes: e.detail.value });
  },

  onReturnServiceFeeInput(e) {
    this.setData({ returnServiceFee: e.detail.value });
  },

  onReturnServicePaymentReferenceInput(e) {
    this.setData({ returnServicePaymentReference: e.detail.value });
  },

  onReturnServiceTypeChange(e) {
    this.setData({ returnServiceTypeIndex: Number(e.detail.value) });
  },

  onPaymentStatusChange(e) {
    this.setData({ paymentStatusIndex: Number(e.detail.value) });
  },

  onPaymentChannelChange(e) {
    this.setData({ paymentChannelIndex: Number(e.detail.value) });
  },

  onPickupStatusChange(e) {
    this.setData({ pickupStatusIndex: Number(e.detail.value) });
  },

  markDelivered() {
    const item = this.data.item;
    if (!item || this.data.actionLoading) return;
    wx.showModal({
      title: '确认发货',
      content: `确认将订单 ${item.order_no} 标记为已发货吗？`,
      success: (modalRes) => {
        if (!modalRes.confirm) return;
        this.setData({ actionLoading: true });
        api.request(`/api/mp/staff/orders/${item.id}/deliver/`, {
          method: 'POST',
          data: { ship_tracking: this.data.shipTracking },
          needLogin: true
        }).then(() => {
          wx.showToast({ title: '已标记发货', icon: 'success' });
          this.loadDetail(item.id);
        }).catch(err => {
          wx.showToast({ title: err.error || '操作失败', icon: 'none' });
        }).finally(() => {
          this.setData({ actionLoading: false });
        });
      }
    });
  },

  markReturned() {
    const item = this.data.item;
    if (!item || this.data.actionLoading) return;
    wx.showModal({
      title: '确认归还',
      content: `确认将订单 ${item.order_no} 标记为已归还吗？`,
      success: (modalRes) => {
        if (!modalRes.confirm) return;
        this.setData({ actionLoading: true });
        api.request(`/api/mp/staff/orders/${item.id}/return/`, {
          method: 'POST',
          data: {
            return_tracking: this.data.returnTracking,
            balance_paid: this.data.balancePaid || '0'
          },
          needLogin: true
        }).then(() => {
          wx.showToast({ title: '已标记归还', icon: 'success' });
          this.loadDetail(item.id);
        }).catch(err => {
          wx.showToast({ title: err.error || '操作失败', icon: 'none' });
        }).finally(() => {
          this.setData({ actionLoading: false });
        });
      }
    });
  },

  recordBalance() {
    const item = this.data.item;
    if (!item || this.data.actionLoading) return;
    if (!this.data.balancePaid) {
      wx.showToast({ title: '请填写收款金额', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '登记尾款',
      content: `确认登记订单 ${item.order_no} 的尾款收款吗？`,
      success: (modalRes) => {
        if (!modalRes.confirm) return;
        this.setData({ actionLoading: true });
        api.request(`/api/mp/staff/orders/${item.id}/balance/`, {
          method: 'POST',
          data: {
            amount: this.data.balancePaid,
            notes: this.data.balanceNotes
          },
          needLogin: true
        }).then(() => {
          wx.showToast({ title: '尾款登记成功', icon: 'success' });
          this.setData({ balancePaid: '', balanceNotes: '' });
          this.loadDetail(item.id);
        }).catch(err => {
          wx.showToast({ title: err.error || '登记失败', icon: 'none' });
        }).finally(() => {
          this.setData({ actionLoading: false });
        });
      }
    });
  },

  updateReturnService() {
    const item = this.data.item;
    if (!item || this.data.actionLoading) return;
    const returnServiceType = this.data.returnServiceTypeOptions[this.data.returnServiceTypeIndex].value;
    const returnServicePaymentStatus = this.data.paymentStatusOptions[this.data.paymentStatusIndex].value;
    const returnServicePaymentChannel = this.data.paymentChannelOptions[this.data.paymentChannelIndex].value;
    const returnPickupStatus = this.data.pickupStatusOptions[this.data.pickupStatusIndex].value;
    wx.showModal({
      title: '更新包回邮',
      content: `确认更新订单 ${item.order_no} 的包回邮服务状态吗？`,
      success: (modalRes) => {
        if (!modalRes.confirm) return;
        this.setData({ actionLoading: true });
        api.request(`/api/mp/staff/orders/${item.id}/return-service/`, {
          method: 'POST',
          data: {
            return_service_type: returnServiceType,
            return_service_fee: this.data.returnServiceFee || '0',
            return_service_payment_status: returnServicePaymentStatus,
            return_service_payment_channel: returnServicePaymentChannel,
            return_service_payment_reference: this.data.returnServicePaymentReference,
            return_pickup_status: returnPickupStatus
          },
          needLogin: true
        }).then(() => {
          wx.showToast({ title: '包回邮已更新', icon: 'success' });
          this.loadDetail(item.id);
        }).catch(err => {
          wx.showToast({ title: err.error || '更新失败', icon: 'none' });
        }).finally(() => {
          this.setData({ actionLoading: false });
        });
      }
    });
  },

  copyOrderNo() {
    const item = this.data.item;
    if (!item) return;
    wx.setClipboardData({
      data: item.order_no,
      success: () => wx.showToast({ title: '订单号已复制', icon: 'none' })
    });
  },

  copyPhone() {
    const item = this.data.item;
    if (!item || !item.customer_phone) return;
    wx.setClipboardData({
      data: item.customer_phone,
      success: () => wx.showToast({ title: '手机号已复制', icon: 'none' })
    });
  },

  formatOptionLabel(options, index) {
    return options[index] ? options[index].label : '';
  }
});
