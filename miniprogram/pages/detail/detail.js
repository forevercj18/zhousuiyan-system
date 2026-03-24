const api = require('../../utils/api');

Page({
  data: {
    sku: null,
    loading: true,
    currentImageIndex: 0
  },

  onLoad(options) {
    if (options.id) {
      this.loadDetail(options.id);
    }
  },

  /** 加载产品详情 */
  loadDetail(id) {
    this.setData({ loading: true });
    api.request('/api/mp/skus/' + id + '/').then(data => {
      this.setData({ sku: data, loading: false });
    }).catch(err => {
      this.setData({ loading: false });
      wx.showToast({ title: err.error || '加载失败', icon: 'none' });
    });
  },

  /** 轮播图切换 */
  onSwiperChange(e) {
    this.setData({ currentImageIndex: e.detail.current });
  },

  /** 预览大图 */
  previewImage(e) {
    const urls = this.data.sku.images.map(img => img.url);
    const current = e.currentTarget.dataset.url;
    wx.previewImage({ urls, current });
  },

  /** 我要租 - 跳转下单页 */
  goOrder() {
    const sku = this.data.sku;
    if (sku.stock_status === 'soldout') {
      wx.showToast({ title: '该套装已售罄', icon: 'none' });
      return;
    }
    wx.navigateTo({
      url: '/pages/order/order?id=' + sku.id + '&name=' + encodeURIComponent(sku.name) + '&price=' + sku.rental_price + '&deposit=' + sku.deposit
    });
  }
});
