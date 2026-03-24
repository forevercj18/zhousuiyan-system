const api = require('../../utils/api');
const app = getApp();

Page({
  data: {
    username: '',
    password: '',
    submitting: false
  },

  onLoad() {
    if (!app.globalData.isLogin) {
      api.login().catch(() => {});
    }
  },

  onUsernameInput(e) {
    this.setData({ username: e.detail.value });
  },

  onPasswordInput(e) {
    this.setData({ password: e.detail.value });
  },

  submitBind() {
    const { username, password, submitting } = this.data;
    if (submitting) return;
    if (!username || !password) {
      wx.showToast({ title: '请输入账号和密码', icon: 'none' });
      return;
    }
    this.setData({ submitting: true });
    api.ensureLogin()
      .then(() => api.bindStaffAccount(username, password))
      .then(() => {
        api.setPreferredMode('work');
        wx.showToast({ title: '绑定成功', icon: 'success' });
        setTimeout(() => {
          wx.redirectTo({ url: '/pages/work-home/work-home' });
        }, 500);
      })
      .catch(err => {
        wx.showToast({ title: err.error || '绑定失败', icon: 'none' });
      })
      .finally(() => this.setData({ submitting: false }));
  }
});
