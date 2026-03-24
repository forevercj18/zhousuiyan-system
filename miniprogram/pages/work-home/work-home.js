const api = require('../../utils/api');

Page({
  data: {
    loading: true,
    needBind: false,
    staff: null,
    shortcuts: [],
    groupedShortcuts: [],
    busy: false,
    urgentSummary: null
  },

  onShow() {
    api.setPreferredMode('work');
    this.loadData();
  },

  onPullDownRefresh() {
    this.loadData(true);
  },

  loadData(fromPullDown = false) {
    this.setData({ loading: true });
    api.ensureLogin()
      .then(() => api.getStaffProfile())
      .then(profile => {
        if (!profile.is_staff_bound) {
          this.setData({ loading: false, needBind: true, staff: null, shortcuts: [], groupedShortcuts: [] });
          if (fromPullDown) wx.stopPullDownRefresh();
          return null;
        }
        this.setData({ needBind: false, staff: profile.staff });
        return api.request('/api/mp/staff/dashboard/', { needLogin: true });
      })
      .then(data => {
        if (!data) return;
        const shortcuts = data.shortcuts || [];
        this.setData({
          loading: false,
          shortcuts,
          groupedShortcuts: this.buildShortcutGroups(shortcuts),
          urgentSummary: this.buildUrgentSummary(shortcuts),
          staff: data.staff || this.data.staff
        });
        if (fromPullDown) wx.stopPullDownRefresh();
      })
      .catch(err => {
        this.setData({ loading: false });
        if (fromPullDown) wx.stopPullDownRefresh();
        wx.showToast({ title: err.error || '加载失败', icon: 'none' });
      });
  },

  buildShortcutGroups(shortcuts) {
    const groups = [
      { title: '客服待办', keys: ['today_contact', 'overdue_contact', 'ready_to_convert', 'converted_waiting_ship'] },
      { title: '履约跟进', keys: ['waiting_ship', 'shipment_overdue', 'balance_pending', 'return_service_pending'] }
    ];
    return groups.map(group => ({
      title: group.title,
      items: shortcuts.filter(item => group.keys.includes(item.key)),
      totalCount: shortcuts
        .filter(item => group.keys.includes(item.key))
        .reduce((sum, item) => sum + Number(item.count || 0), 0),
      urgentCount: shortcuts
        .filter(item => group.keys.includes(item.key) && ['overdue_contact', 'shipment_overdue', 'balance_pending'].includes(item.key))
        .reduce((sum, item) => sum + Number(item.count || 0), 0)
    })).filter(group => group.items.length);
  },

  buildUrgentSummary(shortcuts) {
    const urgentKeys = ['overdue_contact', 'shipment_overdue', 'balance_pending'];
    const urgentItems = (shortcuts || []).filter(item => urgentKeys.includes(item.key) && Number(item.count || 0) > 0);
    const total = urgentItems.reduce((sum, item) => sum + Number(item.count || 0), 0);
    if (!total) return null;
    return {
      total,
      labels: urgentItems.map(item => `${item.label}${item.count}`)
    };
  },

  goBind() {
    wx.redirectTo({ url: '/pages/work-bind/work-bind' });
  },

  goCustomerMode() {
    api.setPreferredMode('customer');
    wx.switchTab({
      url: '/pages/index/index'
    });
  },

  goTarget(e) {
    const url = e.currentTarget.dataset.url;
    if (url) {
      wx.navigateTo({ url });
    }
  },

  goSearch() {
    wx.navigateTo({ url: '/pages/work-search/work-search' });
  }
});
