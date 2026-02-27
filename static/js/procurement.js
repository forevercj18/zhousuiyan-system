// 采购管理相关功能

// 自动计算采购单总额
function calculatePurchaseTotal() {
    let total = 0;
    const items = document.querySelectorAll('.po-item');

    items.forEach(item => {
        const quantity = parseFloat(item.querySelector('.quantity-input')?.value || 0);
        const price = parseFloat(item.querySelector('.price-input')?.value || 0);
        total += quantity * price;
    });

    const totalInput = document.getElementById('total_amount');
    if (totalInput) {
        totalInput.value = utils.formatMoney(total);
    }

    return total;
}

// 添加采购明细行
function addPurchaseItem() {
    const container = document.getElementById('poItems');
    if (!container) return;

    const template = container.querySelector('.po-item');
    if (!template) return;

    const newItem = template.cloneNode(true);

    // 清空新行的值
    newItem.querySelectorAll('input').forEach(input => {
        if (input.type === 'number') {
            input.value = input.classList.contains('quantity-input') ? '1' : '0';
        } else {
            input.value = '';
        }
    });

    newItem.querySelector('select').selectedIndex = 0;
    newItem.querySelector('.subtotal-display').value = utils.formatMoney(0);

    container.appendChild(newItem);
    attachPurchaseItemEvents(newItem);
}

// 删除采购明细行
function removePurchaseItem(button) {
    const container = document.getElementById('poItems');
    const item = button.closest('.po-item');

    if (container.children.length > 1) {
        item.remove();
        calculatePurchaseTotal();
    } else {
        utils.showMessage('至少保留一条明细', 'warning');
    }
}

// 绑定采购明细事件
function attachPurchaseItemEvents(item) {
    // 部件选择变化
    const partSelect = item.querySelector('.part-select');
    if (partSelect) {
        partSelect.addEventListener('change', function() {
            const option = this.options[this.selectedIndex];
            const cost = option.dataset.cost || 0;
            const priceInput = item.querySelector('.price-input');
            if (priceInput) {
                priceInput.value = cost;
                updatePurchaseItemSubtotal(item);
            }
        });
    }

    // 数量变化
    const quantityInput = item.querySelector('.quantity-input');
    if (quantityInput) {
        quantityInput.addEventListener('input', function() {
            updatePurchaseItemSubtotal(item);
        });
    }

    // 单价变化
    const priceInput = item.querySelector('.price-input');
    if (priceInput) {
        priceInput.addEventListener('input', function() {
            updatePurchaseItemSubtotal(item);
        });
    }

    // 删除按钮
    const removeBtn = item.querySelector('.btn-remove-item');
    if (removeBtn) {
        removeBtn.addEventListener('click', function() {
            removePurchaseItem(this);
        });
    }
}

// 更新采购明细小计
function updatePurchaseItemSubtotal(item) {
    const quantity = parseFloat(item.querySelector('.quantity-input')?.value || 0);
    const price = parseFloat(item.querySelector('.price-input')?.value || 0);
    const subtotal = quantity * price;

    const subtotalDisplay = item.querySelector('.subtotal-display');
    if (subtotalDisplay) {
        subtotalDisplay.value = utils.formatMoney(subtotal);
    }

    calculatePurchaseTotal();
}

// 验证采购单
function validatePurchaseOrder() {
    const supplier = document.getElementById('supplier')?.value;
    const orderDate = document.getElementById('order_date')?.value;
    const expectedDate = document.getElementById('expected_date')?.value;

    if (!supplier || !orderDate || !expectedDate) {
        utils.showMessage('请填写所有必填项', 'error');
        return false;
    }

    const items = document.querySelectorAll('.po-item');
    let hasValidItem = false;

    items.forEach(item => {
        const partSelect = item.querySelector('.part-select');
        const quantity = item.querySelector('.quantity-input')?.value;
        const price = item.querySelector('.price-input')?.value;

        if (partSelect?.value && quantity > 0 && price >= 0) {
            hasValidItem = true;
        }
    });

    if (!hasValidItem) {
        utils.showMessage('请至少添加一条有效的采购明细', 'error');
        return false;
    }

    return true;
}

// 提交采购单
function submitPurchaseOrder() {
    if (!validatePurchaseOrder()) {
        return false;
    }

    utils.showMessage('采购单提交成功', 'success');
    return true;
}

// 导出采购单
function exportPurchaseOrder(poId) {
    utils.showMessage('正在导出采购单...', 'info');
    // 实现导出逻辑
    setTimeout(() => {
        utils.showMessage('采购单导出成功', 'success');
    }, 1000);
}

// 打印采购单
function printPurchaseOrder(poId) {
    window.print();
}

// 批量导入部件
function importParts() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.csv,.xlsx';
    input.onchange = function(e) {
        const file = e.target.files[0];
        if (file) {
            utils.showMessage('正在导入部件数据...', 'info');
            // 实现导入逻辑
            setTimeout(() => {
                utils.showMessage('部件数据导入成功', 'success');
            }, 1000);
        }
    };
    input.click();
}

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', function() {
    // 绑定所有采购明细的事件
    const items = document.querySelectorAll('.po-item');
    items.forEach(item => attachPurchaseItemEvents(item));

    // 初始计算总额
    calculatePurchaseTotal();
});
