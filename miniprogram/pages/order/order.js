const api = require('../../utils/api');

Page({
  data: {
    skuId: '',
    skuName: '',
    skuPrice: '',
    skuDeposit: '',
    // 表单字段
    customerWechat: '',
    customerName: '',
    customerPhone: '',
    city: '',
    deliveryAddress: '',
    eventDate: '',
    quantity: 1,
    notes: '',
    // 日期选择器最小日期（明天）
    minDate: '',
    submitting: false
  },

  onLoad(options) {
    // 计算明天的日期作为最小可选日期
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    const minDate = this.formatDate(tomorrow);

    this.setData({
      skuId: options.id || '',
      skuName: decodeURIComponent(options.name || ''),
      skuPrice: options.price || '',
      skuDeposit: options.deposit || '',
      minDate: minDate
    });
  },

  formatDate(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return y + '-' + m + '-' + day;
  },

  // 表单输入绑定
  onInputWechat(e)  { this.setData({ customerWechat: e.detail.value }); },
  onInputName(e)    { this.setData({ customerName: e.detail.value }); },
  onInputPhone(e)   { this.setData({ customerPhone: e.detail.value }); },
  onInputCity(e)    { this.setData({ city: e.detail.value }); },
  onInputAddress(e) { this.setData({ deliveryAddress: e.detail.value }); },
  onInputNotes(e)   { this.setData({ notes: e.detail.value }); },

  onGetPhoneNumber(e) {
    const detail = e.detail || {};
    if (!detail.code) {
      wx.showToast({ title: '未获取到手机号授权', icon: 'none' });
      return;
    }
    api.ensureLogin()
      .then(() => api.syncPhoneNumber(detail.code))
      .then(data => {
        this.setData({ customerPhone: data.phone || '' });
        wx.showToast({ title: '手机号已获取', icon: 'success' });
      })
      .catch(err => {
        wx.showToast({ title: err.error || '手机号获取失败', icon: 'none' });
      });
  },

  onDateChange(e) {
    this.setData({ eventDate: e.detail.value });
  },

  onQuantityMinus() {
    if (this.data.quantity > 1) {
      this.setData({ quantity: this.data.quantity - 1 });
    }
  },
  onQuantityPlus() {
    if (this.data.quantity < 10) {
      this.setData({ quantity: this.data.quantity + 1 });
    }
  },

  /** 提交意向订单 */
  onSubmit() {
    const { skuId, customerWechat, customerName, customerPhone, city, deliveryAddress, eventDate, quantity, notes } = this.data;

    // 校验必填项
    if (!customerWechat.trim()) {
      wx.showToast({ title: '请填写微信号', icon: 'none' }); return;
    }
    if (!eventDate) {
      wx.showToast({ title: '请选择活动日期', icon: 'none' }); return;
    }
    if (!customerPhone.trim()) {
      wx.showToast({ title: '请填写手机号', icon: 'none' }); return;
    }
    if (!deliveryAddress.trim()) {
      wx.showToast({ title: '请填写收货地址', icon: 'none' }); return;
    }

    this.setData({ submitting: true });

    // 确保已登录
    api.ensureLogin().then(() => {
      return api.request('/api/mp/reservations/', {
        method: 'POST',
        needLogin: true,
        data: {
          sku_id: parseInt(skuId),
          event_date: eventDate,
          customer_wechat: customerWechat.trim(),
          customer_name: customerName.trim(),
          customer_phone: customerPhone.trim(),
          city: city.trim(),
          delivery_address: deliveryAddress.trim(),
          quantity: quantity,
          notes: notes.trim()
        }
      });
    }).then(data => {
      this.setData({ submitting: false });
      wx.showModal({
        title: '提交成功',
        content: '意向订单已提交，客服将尽快通过微信与您联系确认。',
        showCancel: false,
        success() {
          wx.navigateBack();
        }
      });
    }).catch(err => {
      this.setData({ submitting: false });
      if (err.needLogin) {
        wx.showToast({ title: '请先授权登录', icon: 'none' });
      } else {
        wx.showToast({ title: err.error || '提交失败', icon: 'none' });
      }
    });
  }
});
