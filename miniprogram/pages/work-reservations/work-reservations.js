const api = require('../../utils/api');

Page({
  data: {
    loading: true,
    actionLoadingId: null,
    keyword: '',
    status: '',
    contact: '',
    journey: '',
    source: '',
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
      contact: options.contact || '',
      journey: options.journey || '',
      source: options.source || '',
      ownerId: options.owner_id || ''
    });
    this.loadItems();
  },

  onShow() {
    if (!this.data.loading) {
      this.loadItems();
    }
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
    if (key === 'today') {
      updates.contact = 'today';
      updates.status = '';
      updates.journey = '';
    } else if (key === 'overdue') {
      updates.contact = 'overdue';
      updates.status = '';
      updates.journey = '';
    } else if (key === 'ready') {
      updates.status = 'ready_to_convert';
      updates.contact = '';
      updates.journey = '';
    } else if (key === 'awaiting') {
      updates.journey = 'awaiting_shipment';
      updates.status = '';
      updates.contact = '';
    } else {
      updates.status = '';
      updates.contact = '';
      updates.journey = '';
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
    this.setData({ sourceIndex: index, source: option.value || '' });
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
    ['status', 'contact', 'journey', 'keyword', 'source'].forEach(key => {
      if (this.data[key]) params.push(`${key}=${encodeURIComponent(this.data[key])}`);
    });
    if (this.data.ownerId) params.push(`owner_id=${encodeURIComponent(this.data.ownerId)}`);
    const path = `/api/mp/staff/reservations/${params.length ? `?${params.join('&')}` : ''}`;
    api.request(path, { needLogin: true }).then(data => {
      const sourceOptions = [{ value: '', label: '全部来源' }].concat((data.filters && data.filters.sources) || []);
      const ownerOptions = [{ value: '', label: '全部负责人' }].concat((data.filters && data.filters.owners) || []);
      this.setData({
        items: data.results || [],
        loading: false,
        sourceOptions,
        ownerOptions,
        sourceIndex: this.resolvePickerIndex(sourceOptions, this.data.source),
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

  copyWechat(e) {
    const wechat = e.currentTarget.dataset.wechat || '';
    if (!wechat) return;
    wx.setClipboardData({
      data: wechat,
      success: () => wx.showToast({ title: '微信号已复制', icon: 'none' })
    });
  },

  quickUpdateStatus(e) {
    const id = e.currentTarget.dataset.id;
    const status = e.currentTarget.dataset.status;
    const statusLabel = e.currentTarget.dataset.label;
    if (!id || !status) return;
    wx.showModal({
      title: '确认操作',
      content: `确定将这张预定单标记为“${statusLabel}”吗？`,
      success: res => {
        if (!res.confirm) return;
        this.setData({ actionLoadingId: id });
        api.request(`/api/mp/staff/reservations/${id}/status/`, {
          method: 'POST',
          data: { status },
          needLogin: true
        }).then(data => {
          wx.showToast({ title: data.message || '状态已更新', icon: 'none' });
          this.setData({ actionLoadingId: null });
          this.loadItems();
        }).catch(err => {
          this.setData({ actionLoadingId: null });
          wx.showToast({ title: err.error || '更新失败', icon: 'none' });
        });
      }
    });
  },

  goDetail(e) {
    wx.navigateTo({ url: `/pages/work-reservation-detail/work-reservation-detail?id=${e.currentTarget.dataset.id}` });
  }
});
