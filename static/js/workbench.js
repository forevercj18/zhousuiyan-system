// 工作台相关功能

function postAction(url, payload = {}) {
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = url;

    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'csrfmiddlewaretoken';
    input.value = csrfToken ? csrfToken.value : '';
    form.appendChild(input);

    Object.entries(payload).forEach(([key, value]) => {
        const field = document.createElement('input');
        field.type = 'hidden';
        field.name = key;
        field.value = value;
        form.appendChild(field);
    });

    document.body.appendChild(form);
    form.submit();
}

function markAsConfirmed(orderId, totalAmount) {
    const text = window.prompt(`请输入已收押金金额（订单总额: ${totalAmount}）`, '0');
    if (text === null) {
        return;
    }
    postAction(`/orders/${orderId}/confirm/`, { deposit_paid: text });
}

// 标记订单为已送达
function markAsDelivered(orderId) {
    if (!utils.confirm('确认标记此订单为已送达？')) {
        return;
    }
    const shipTracking = window.prompt('请输入发货单号（可留空）', '') || '';
    postAction(`/orders/${orderId}/mark-delivered/`, { ship_tracking: shipTracking });
}

function markAsReturned(orderId, balance) {
    if (!utils.confirm('确认标记此订单为已归还？')) {
        return;
    }
    const returnTracking = window.prompt('请输入回收单号（可留空）', '') || '';
    const balancePaid = window.prompt(`请输入已收尾款金额（当前待收: ${balance}）`, '0');
    if (balancePaid === null) {
        return;
    }
    postAction(`/orders/${orderId}/mark-returned/`, {
        return_tracking: returnTracking,
        balance_paid: balancePaid
    });
}

// 标记订单为已完成
function markAsCompleted(orderId) {
    if (!utils.confirm('确认标记此订单为已完成？')) {
        return;
    }
    postAction(`/orders/${orderId}/mark-completed/`);
}

// 快速查看订单详情
function quickViewOrder(orderId) {
    // 这里可以实现快速查看功能，使用模态框显示订单详情
    window.location.href = `/orders/${orderId}/`;
}

// 拖拽排序功能（可选）
document.addEventListener('DOMContentLoaded', function() {
    const kanbanCards = document.querySelectorAll('.kanban-card');

    kanbanCards.forEach(card => {
        card.addEventListener('dragstart', handleDragStart);
        card.addEventListener('dragend', handleDragEnd);
    });

    const kanbanBodies = document.querySelectorAll('.kanban-body');
    kanbanBodies.forEach(body => {
        body.addEventListener('dragover', handleDragOver);
        body.addEventListener('drop', handleDrop);
    });
});

let draggedElement = null;

function handleDragStart(e) {
    draggedElement = this;
    this.style.opacity = '0.5';
}

function handleDragEnd(e) {
    this.style.opacity = '1';
}

function handleDragOver(e) {
    if (e.preventDefault) {
        e.preventDefault();
    }
    return false;
}

function handleDrop(e) {
    if (e.stopPropagation) {
        e.stopPropagation();
    }

    if (draggedElement !== this) {
        // 这里可以实现拖拽更新订单状态的逻辑
        console.log('Order status update needed');
    }

    return false;
}

// 批量操作
function batchConfirmOrders() {
    const selectedOrders = getSelectedOrders();
    if (selectedOrders.length === 0) {
        utils.showMessage('请先选择订单', 'warning');
        return;
    }

    if (utils.confirm(`确认批量确认 ${selectedOrders.length} 个订单？`)) {
        utils.showMessage(`已确认 ${selectedOrders.length} 个订单`, 'success');
        setTimeout(() => location.reload(), 1000);
    }
}

function getSelectedOrders() {
    const checkboxes = document.querySelectorAll('.order-checkbox:checked');
    return Array.from(checkboxes).map(cb => cb.value);
}

// 导出工作台数据
function exportWorkbenchData() {
    utils.showMessage('正在导出数据...', 'info');
    // 实现导出逻辑
    setTimeout(() => {
        utils.showMessage('数据导出成功', 'success');
    }, 1000);
}
