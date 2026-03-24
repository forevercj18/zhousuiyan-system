const api = require('../../utils/api');

Page({
  data: {
    loading: true,
    keyword: '',
    status: '',
    followup: '',
    orderSource: '',
    ownerId: '',
    activeQuickFilter: '',
    items: [],
    sourceOptions: [],
    ownerOptions: [],
    sourceIndex: 0,
    ownerIndex: 0
  },

  onLoad(options) {
    this.setData({
      status: options.status || '',
      followup: options.followup || '',
      orderSource: options.order_source || '',
      ownerId: options.owner_id || ''
    });
    this.loadItems();
  },

  onShow() {
    if (!this.data.loading) this.loadItems();
  },

  onPullDownRefresh() {
    this.loadItems(true);
  },

  onKeywordInput(e) {
    this.setData({ keyword: e.detail.value });
  },

  onSearch() {
    this.loadItems();
  },

  applyQuickFilter(e) {
    const key = e.currentTarget.dataset.key || '';
    const updates = { activeQuickFilter: key };
    if (key === 'waiting_ship') {
      updates.followup = 'waiting_ship';
      updates.status = '';
    } else if (key === 'shipment_overdue') {
      updates.followup = 'shipment_overdue';
      updates.status = '';
    } else if (key === 'balance_pending') {
      updates.followup = 'balance_pending';
      updates.status = '';
    } else if (key === 'return_service_pending') {
      updates.followup = 'return_service_pending';
      updates.status = '';
    } else {
      updates.followup = '';
      updates.status = '';
    }
    this.setData(updates);
    this.loadItems();
  },

  clearSearch() {
    this.setData({ keyword: '' });
    this.loadItems();
  },

  onSourceChange(e) {
    const index = Number(e.detail.value || 0);
    const option = (this.data.sourceOptions || [])[index] || { value: '' };
    this.setData({ sourceIndex: index, orderSource: option.value || '' });
    this.loadItems();
  },

  onOwnerChange(e) {
    const index = Number(e.detail.value || 0);
    const option = (this.data.ownerOptions || [])[index] || { value: '' };
    this.setData({ ownerIndex: index, ownerId: option.value ? String(option.value) : '' });
    this.loadItems();
  },

  loadItems(fromPullDown = false) {
    this.setData({ loading: true });
    const params = [];
    ['status', 'followup', 'keyword'].forEach(key => {
      if (this.data[key]) params.push(`${key}=${encodeURIComponent(this.data[key])}`);
    });
    if (this.data.orderSource) params.push(`order_source=${encodeURIComponent(this.data.orderSource)}`);
    if (this.data.ownerId) params.push(`owner_id=${encodeURIComponent(this.data.ownerId)}`);
    const path = `/api/mp/staff/orders/${params.length ? `?${params.join('&')}` : ''}`;
    api.request(path, { needLogin: true }).then(data => {
      const sourceOptions = [{ value: '', label: '全部来源' }].concat((data.filters && data.filters.sources) || []);
      const ownerOptions = [{ value: '', label: '全部负责人' }].concat((data.filters && data.filters.owners) || []);
      this.setData({
        items: data.results || [],
        loading: false,
        sourceOptions,
        ownerOptions,
        sourceIndex: this.resolvePickerIndex(sourceOptions, this.data.orderSource),
        ownerIndex: this.resolvePickerIndex(ownerOptions, this.data.ownerId)
      });
      if (fromPullDown) wx.stopPullDownRefresh();
    }).catch(err => {
      this.setData({ loading: false });
      if (fromPullDown) wx.stopPullDownRefresh();
      wx.showToast({ title: err.error || '加载失败', icon: 'none' });
    });
  },

  resolvePickerIndex(options, value) {
    const normalized = value === null || value === undefined ? '' : String(value);
    const index = (options || []).findIndex(item => String(item.value || '') === normalized);
    return index >= 0 ? index : 0;
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

  copyOrderNo(e) {
    const orderNo = e.currentTarget.dataset.orderNo || '';
    if (!orderNo) return;
    wx.setClipboardData({
      data: orderNo,
      success: () => wx.showToast({ title: '订单号已复制', icon: 'none' })
    });
  },

  goAction(e) {
    const id = e.currentTarget.dataset.id;
    const action = e.currentTarget.dataset.action;
    if (!id) return;
    const suffix = action ? `?id=${id}&action=${action}` : `?id=${id}`;
    wx.navigateTo({ url: `/pages/work-order-detail/work-order-detail${suffix}` });
  },

  goDetail(e) {
    wx.navigateTo({ url: `/pages/work-order-detail/work-order-detail?id=${e.currentTarget.dataset.id}` });
  }
});
