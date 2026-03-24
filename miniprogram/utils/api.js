/**
 * API 请求封装
 */
const app = getApp();

/**
 * 发起 API 请求
 * @param {string} path - 接口路径，如 '/api/mp/skus/'
 * @param {object} options - { method, data, needLogin }
 */
function request(path, options = {}) {
  const { method = 'GET', data = {}, needLogin = false } = options;

  return new Promise((resolve, reject) => {
    // 需要登录但未登录
    if (needLogin && !app.globalData.token) {
      reject({ error: '请先登录', needLogin: true });
      return;
    }

    const header = { 'Content-Type': 'application/json' };
    if (app.globalData.token) {
      header['Authorization'] = 'Bearer ' + app.globalData.token;
    }

    wx.request({
      url: app.globalData.baseUrl + path,
      method: method,
      data: data,
      header: header,
      success(res) {
        if (res.statusCode === 200 || res.statusCode === 201) {
          resolve(res.data);
        } else if (res.statusCode === 401) {
          // Token 过期，清除登录状态
          app.globalData.token = '';
          app.globalData.isLogin = false;
          wx.removeStorageSync('token');
          reject({ error: res.data.error || '登录已过期', needLogin: true });
        } else {
          reject({ error: res.data.error || '请求失败', statusCode: res.statusCode });
        }
      },
      fail(err) {
        reject({ error: '网络错误，请检查网络连接' });
      }
    });
  });
}

/**
 * 微信登录
 */
function login() {
  return new Promise((resolve, reject) => {
    wx.login({
      success(loginRes) {
        if (!loginRes.code) {
          reject({ error: '微信登录失败' });
          return;
        }
        request('/api/mp/login/', {
          method: 'POST',
          data: { code: loginRes.code }
        }).then(data => {
          // 保存 token
          app.globalData.token = data.token;
          app.globalData.isLogin = true;
          app.globalData.userInfo = data.customer;
          app.globalData.staffProfile = data.staff_bound ? (app.globalData.staffProfile || null) : null;
          wx.setStorageSync('token', data.token);
          wx.setStorageSync('userInfo', data.customer);
          resolve(data);
        }).catch(reject);
      },
      fail() {
        reject({ error: '微信登录失败' });
      }
    });
  });
}

/**
 * 确保已登录，未登录则自动登录
 */
function ensureLogin() {
  if (app.globalData.isLogin && app.globalData.token) {
    return Promise.resolve();
  }
  return login();
}

function getStaffProfile() {
  return request('/api/mp/staff/profile/', { needLogin: true }).then(data => {
    app.globalData.staffProfile = data.staff || null;
    wx.setStorageSync('staffProfile', data.staff || '');
    return data;
  });
}

function bindStaffAccount(username, password) {
  return request('/api/mp/staff/bind/', {
    method: 'POST',
    data: { username, password },
    needLogin: true
  }).then(data => {
    app.globalData.staffProfile = data.staff || null;
    wx.setStorageSync('staffProfile', data.staff || '');
    return data;
  });
}

function setPreferredMode(mode) {
  app.globalData.preferredMode = mode;
  wx.setStorageSync('preferredMode', mode);
}

function getPreferredMode() {
  return app.globalData.preferredMode || wx.getStorageSync('preferredMode') || 'customer';
}

module.exports = {
  request,
  login,
  ensureLogin,
  getStaffProfile,
  bindStaffAccount,
  setPreferredMode,
  getPreferredMode
};
