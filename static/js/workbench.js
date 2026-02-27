// 工作台相关功能

function getCsrfToken() {
    const tokenInput = document.querySelector('[name=csrfmiddlewaretoken]');
    if (tokenInput && tokenInput.value) {
        return tokenInput.value;
    }

    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (match && match[1]) {
        return decodeURIComponent(match[1]);
    }

    return '';
}

function postAction(url, payload = {}) {
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = url;

    const csrfToken = getCsrfToken();
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'csrfmiddlewaretoken';
    input.value = csrfToken;
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
    if (window.Swal) {
        Swal.fire({
            title: '录入运单并发货',
            html: `
                <div style="text-align:left;display:grid;gap:10px;">
                    <label style="font-size:13px;color:#666;">已收押金（订单金额: ${totalAmount}）</label>
                    <input id="swal-deposit" class="swal2-input" placeholder="如未收押金填 0" value="0" style="margin:0;width:100%;">
                    <label style="font-size:13px;color:#666;">快递单号 <span style="color:#e53935;">*</span></label>
                    <input id="swal-tracking" class="swal2-input" placeholder="请输入快递单号" style="margin:0;width:100%;">
                </div>
            `,
            focusConfirm: false,
            confirmButtonText: '确认发货',
            cancelButtonText: '取消',
            showCancelButton: true,
            preConfirm: () => {
                const deposit = (document.getElementById('swal-deposit') || {}).value || '0';
                const tracking = ((document.getElementById('swal-tracking') || {}).value || '').trim();
                if (!tracking) {
                    Swal.showValidationMessage('快递单号不能为空');
                    return false;
                }
                return { deposit, tracking };
            }
        }).then((result) => {
            if (!result.isConfirmed) {
                return;
            }
            postAction(`/orders/${orderId}/confirm/`, {
                deposit_paid: result.value.deposit,
                ship_tracking: result.value.tracking,
                auto_deliver: '1'
            });
        });
        return;
    }

    const text = window.prompt(`请输入已收押金金额（订单总额: ${totalAmount}）`, '0');
    if (text === null) return;
    const shipTracking = (window.prompt('请输入快递单号（必填）', '') || '').trim();
    if (!shipTracking) return;
    postAction(`/orders/${orderId}/confirm/`, { deposit_paid: text, ship_tracking: shipTracking, auto_deliver: '1' });
}

// 标记订单为已发货
function markAsDelivered(orderId) {
    if (window.Swal) {
        Swal.fire({
            title: '标记为已发货',
            text: '请输入发货单号（可选）',
            input: 'text',
            inputPlaceholder: '发货单号',
            showCancelButton: true,
            confirmButtonText: '确认',
            cancelButtonText: '取消'
        }).then((result) => {
            if (!result.isConfirmed) return;
            postAction(`/orders/${orderId}/mark-delivered/`, { ship_tracking: result.value || '' });
        });
        return;
    }

    if (!utils.confirm('确认标记此订单为已送达？')) return;
    const shipTracking = window.prompt('请输入发货单号（可留空）', '') || '';
    postAction(`/orders/${orderId}/mark-delivered/`, { ship_tracking: shipTracking });
}

function markAsReturned(orderId, balance) {
    if (window.Swal) {
        Swal.fire({
            title: '标记归还',
            html: `
                <div style="text-align:left;display:grid;gap:10px;">
                    <label style="font-size:13px;color:#666;">回收单号（可选）</label>
                    <input id="swal-return-tracking" class="swal2-input" placeholder="回收单号" style="margin:0;width:100%;">
                    <label style="font-size:13px;color:#666;">已收尾款（当前待收: ${balance}）</label>
                    <input id="swal-balance-paid" class="swal2-input" placeholder="0" value="0" style="margin:0;width:100%;">
                </div>
            `,
            showCancelButton: true,
            confirmButtonText: '确认归还',
            cancelButtonText: '取消',
            preConfirm: () => ({
                returnTracking: ((document.getElementById('swal-return-tracking') || {}).value || '').trim(),
                balancePaid: (document.getElementById('swal-balance-paid') || {}).value || '0'
            })
        }).then((result) => {
            if (!result.isConfirmed) return;
            postAction(`/orders/${orderId}/mark-returned/`, {
                return_tracking: result.value.returnTracking,
                balance_paid: result.value.balancePaid
            });
        });
        return;
    }

    if (!utils.confirm('确认标记此订单为已归还？')) return;
    const returnTracking = window.prompt('请输入回收单号（可留空）', '') || '';
    const balancePaid = window.prompt(`请输入已收尾款金额（当前待收: ${balance}）`, '0');
    if (balancePaid === null) return;
    postAction(`/orders/${orderId}/mark-returned/`, { return_tracking: returnTracking, balance_paid: balancePaid });
}

// 标记订单为已完成
function markAsCompleted(orderId) {
    if (window.Swal) {
        Swal.fire({
            title: '确认完成订单？',
            text: '此操作会将订单状态标记为已完成',
            icon: 'question',
            showCancelButton: true,
            confirmButtonText: '确认完成',
            cancelButtonText: '取消'
        }).then((result) => {
            if (!result.isConfirmed) return;
            postAction(`/orders/${orderId}/mark-completed/`);
        });
        return;
    }

    if (!utils.confirm('确认标记此订单为已完成？')) return;
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
