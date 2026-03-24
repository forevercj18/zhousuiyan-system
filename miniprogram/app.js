/**
 * 宝宝周岁宴道具租赁 - 微信小程序
 */
App({
  onLaunch() {
    // 检查登录状态
    const token = wx.getStorageSync('token');
    const userInfo = wx.getStorageSync('userInfo');
    const staffProfile = wx.getStorageSync('staffProfile');
    const preferredMode = wx.getStorageSync('preferredMode');
    if (token) {
      this.globalData.token = token;
      this.globalData.isLogin = true;
      this.globalData.userInfo = userInfo || null;
      this.globalData.staffProfile = staffProfile || null;
    }
    this.globalData.preferredMode = preferredMode || 'customer';
  },

  globalData: {
    token: '',
    isLogin: false,
    userInfo: null,
    staffProfile: null,
    preferredMode: 'customer',
    // 后端 API 地址（真机/预览必须使用已备案并在微信后台配置的 HTTPS 域名）
    baseUrl: 'https://erp.yanli.net.cn'
  }
});
