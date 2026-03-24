const api = require('../../utils/api');

Page({
  data: {
    loading: true,
    item: null,
    actionLoading: false,
    customerName: '',
    customerPhone: '',
    deliveryAddress: '',
    notes: '',
    ownerOptions: [],
    ownerIndex: 0,
    transferReason: ''
  },

  onLoad(options) {
    if (options.id) this.loadDetail(options.id);
  },

  loadDetail(id) {
    this.setData({ loading: true });
    api.request(`/api/mp/staff/reservations/${id}/`, { needLogin: true }).then(data => {
      this.setData({
        item: data,
        loading: false,
        customerName: data.customer_name || '',
        customerPhone: data.customer_phone || '',
        deliveryAddress: data.delivery_address || '',
        notes: data.notes || '',
        ownerOptions: data.owner_options || [],
        ownerIndex: this.resolveOwnerIndex(data.owner_options || [], data.owner_id),
        transferReason: ''
      });
    }).catch(err => {
      this.setData({ loading: false });
      wx.showToast({ title: err.error || '加载失败', icon: 'none' });
    });
  },

  onCustomerNameInput(e) {
    this.setData({ customerName: e.detail.value });
  },

  onCustomerPhoneInput(e) {
    this.setData({ customerPhone: e.detail.value });
  },

  onDeliveryAddressInput(e) {
    this.setData({ deliveryAddress: e.detail.value });
  },

  onNotesInput(e) {
    this.setData({ notes: e.detail.value });
  },

  onOwnerChange(e) {
    this.setData({ ownerIndex: Number(e.detail.value || 0) });
  },

  onTransferReasonInput(e) {
    this.setData({ transferReason: e.detail.value });
  },

  resolveOwnerIndex(options, ownerId) {
    const index = (options || []).findIndex(item => item.id === ownerId);
    return index >= 0 ? index : 0;
  },

  updateStatus(e) {
    const status = e.currentTarget.dataset.status;
    const item = this.data.item;
    if (!item || !item.can_update_status || this.data.actionLoading) return;
    const label = status === 'ready_to_convert' ? '可转正式订单' : '待补信息';
    wx.showModal({
      title: '确认操作',
      content: `确认将预定单状态更新为“${label}”吗？`,
      success: (modalRes) => {
        if (!modalRes.confirm) return;
        this.setData({ actionLoading: true });
        api.request(`/api/mp/staff/reservations/${item.id}/status/`, {
          method: 'POST',
          data: { status },
          needLogin: true
        }).then(() => {
          wx.showToast({ title: '状态已更新', icon: 'success' });
          this.loadDetail(item.id);
        }).catch(err => {
          wx.showToast({ title: err.error || '更新失败', icon: 'none' });
        }).finally(() => {
          this.setData({ actionLoading: false });
        });
      }
    });
  },

  saveFollowup() {
    const item = this.data.item;
    if (!item || !item.can_update_followup || this.data.actionLoading) return;
    this.setData({ actionLoading: true });
    api.request(`/api/mp/staff/reservations/${item.id}/followup/`, {
      method: 'POST',
      data: {
        customer_name: this.data.customerName,
        customer_phone: this.data.customerPhone,
        delivery_address: this.data.deliveryAddress,
        notes: this.data.notes
      },
      needLogin: true
    }).then(() => {
      wx.showToast({ title: '跟进已保存', icon: 'success' });
      this.loadDetail(item.id);
    }).catch(err => {
      wx.showToast({ title: err.error || '保存失败', icon: 'none' });
    }).finally(() => {
      this.setData({ actionLoading: false });
    });
  },

  transferOwner() {
    const item = this.data.item;
    if (!item || !item.can_transfer_owner || this.data.actionLoading) return;
    const options = this.data.ownerOptions || [];
    const selected = options[this.data.ownerIndex];
    if (!selected) {
      wx.showToast({ title: '暂无可转交负责人', icon: 'none' });
      return;
    }
    if (selected.id === item.owner_id) {
      wx.showToast({ title: '请选择新的负责人', icon: 'none' });
      return;
    }
    wx.showModal({
      title: '确认转交',
      content: `确认将这张预定单转交给“${selected.label}”吗？`,
      success: (modalRes) => {
        if (!modalRes.confirm) return;
        this.setData({ actionLoading: true });
        api.request(`/api/mp/staff/reservations/${item.id}/transfer/`, {
          method: 'POST',
          data: {
            owner_id: selected.id,
            reason: this.data.transferReason
          },
          needLogin: true
        }).then(() => {
          wx.showToast({ title: '负责人已转交', icon: 'success' });
          this.loadDetail(item.id);
        }).catch(err => {
          wx.showToast({ title: err.error || '转交失败', icon: 'none' });
        }).finally(() => {
          this.setData({ actionLoading: false });
        });
      }
    });
  },

  callCustomer() {
    if (!this.data.customerPhone) {
      wx.showToast({ title: '暂无手机号', icon: 'none' });
      return;
    }
    wx.makePhoneCall({
      phoneNumber: this.data.customerPhone,
      fail: () => wx.showToast({ title: '拨号失败', icon: 'none' })
    });
  },

  copyWechat() {
    const item = this.data.item;
    if (!item || !item.customer_wechat) return;
    wx.setClipboardData({
      data: item.customer_wechat,
      success: () => wx.showToast({ title: '微信号已复制', icon: 'none' })
    });
  }
});
