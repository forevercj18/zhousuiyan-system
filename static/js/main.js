// 主JavaScript文件

// 全局工具函数
const utils = {
    // 格式化日期
    formatDate(dateStr) {
        if (!dateStr) return '-';
        const date = new Date(dateStr);
        return date.toLocaleDateString('zh-CN');
    },

    // 格式化金额
    formatMoney(amount) {
        if (amount === null || amount === undefined) return '¥0.00';
        return '¥' + parseFloat(amount).toFixed(2);
    },

    // 显示提示消息
    showMessage(message, type = 'info') {
        if (window.AppUI && window.AppUI.toast) {
            window.AppUI.toast(message, type);
            return;
        }
        const messagesContainer = document.querySelector('.messages') || createMessagesContainer();
        messagesContainer.insertAdjacentHTML('beforeend', `<div class="alert alert-${type}">${message}</div>`);
    },

    // 确认对话框
    confirm(message) {
        return window.confirm(message);
    },

    // AJAX请求封装
    async request(url, options = {}) {
        const defaultOptions = {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
            },
        };

        const config = { ...defaultOptions, ...options };

        // 添加CSRF token
        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
        if (csrfToken && ['POST', 'PUT', 'DELETE'].includes(config.method)) {
            config.headers['X-CSRFToken'] = csrfToken.value;
        }

        try {
            const response = await fetch(url, config);
            const data = await response.json();
            return data;
        } catch (error) {
            console.error('Request failed:', error);
            utils.showMessage('请求失败，请稍后重试', 'error');
            throw error;
        }
    }
};

function escapeHtml(str) {
    return String(str || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

const AppUI = {
    toast(message, type = 'info') {
        const host = document.getElementById('appToastHost');
        if (!host || !window.bootstrap) {
            return;
        }
        const map = {
            success: 'text-bg-success',
            error: 'text-bg-danger',
            danger: 'text-bg-danger',
            warning: 'text-bg-warning',
            info: 'text-bg-primary',
        };
        const klass = map[type] || map.info;
        const wrapper = document.createElement('div');
        wrapper.innerHTML = `
            <div class="toast align-items-center ${klass} border-0" role="alert" aria-live="assertive" aria-atomic="true">
                <div class="d-flex">
                    <div class="toast-body">${escapeHtml(message)}</div>
                    <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
                </div>
            </div>
        `;
        const toastEl = wrapper.firstElementChild;
        host.appendChild(toastEl);
        const toast = new window.bootstrap.Toast(toastEl, { delay: 2600 });
        toast.show();
        toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
    },
    dialog(options = {}) {
        return new Promise((resolve) => {
            const modalEl = document.getElementById('appDialogModal');
            if (!modalEl || !window.bootstrap) {
                resolve({ ok: false, value: '' });
                return;
            }
            const titleEl = document.getElementById('appDialogTitle');
            const msgEl = document.getElementById('appDialogMessage');
            const inputWrap = document.getElementById('appDialogInputWrap');
            const inputEl = document.getElementById('appDialogInput');
            const okBtn = document.getElementById('appDialogOkBtn');
            const cancelBtn = document.getElementById('appDialogCancelBtn');
            const modal = window.bootstrap.Modal.getOrCreateInstance(modalEl, { backdrop: 'static' });
            const needInput = !!options.input;
            titleEl.textContent = options.title || '提示';
            msgEl.innerHTML = escapeHtml(options.message || '').replace(/\n/g, '<br>');
            okBtn.textContent = options.okText || '确定';
            cancelBtn.textContent = options.cancelText || '取消';
            cancelBtn.classList.toggle('d-none', options.hideCancel === true);
            inputWrap.classList.toggle('d-none', !needInput);
            inputEl.value = options.defaultValue || '';
            if (options.placeholder) inputEl.placeholder = options.placeholder;

            let settled = false;
            let pendingResult = null;
            const done = (ok) => {
                if (settled) return;
                settled = true;
                pendingResult = { ok, value: (inputEl.value || '').trim() };
                modal.hide();
            };
            const onHidden = () => {
                modalEl.removeEventListener('hidden.bs.modal', onHidden);
                okBtn.removeEventListener('click', onOk);
                cancelBtn.removeEventListener('click', onCancel);
                resolve(pendingResult || { ok: false, value: '' });
            };
            const onOk = () => done(true);
            const onCancel = () => done(false);
            modalEl.addEventListener('hidden.bs.modal', onHidden);
            okBtn.addEventListener('click', onOk);
            cancelBtn.addEventListener('click', onCancel);
            modal.show();
            if (needInput) setTimeout(() => inputEl.focus(), 120);
        });
    },
    async alert(message, title = '提示') {
        await this.dialog({ title, message, hideCancel: true, okText: '我知道了' });
    },
    async confirm(message, title = '确认') {
        const result = await this.dialog({ title, message });
        return result.ok;
    },
    async prompt(message, title = '请输入', placeholder = '') {
        const result = await this.dialog({ title, message, input: true, placeholder });
        return result.ok ? result.value : null;
    }
};

function createMessagesContainer() {
    const container = document.createElement('div');
    container.className = 'messages';
    const content = document.querySelector('.content');
    if (content) {
        content.insertBefore(container, content.firstChild);
    }
    return container;
}

// 表单验证
function validateForm(formId) {
    const form = document.getElementById(formId);
    if (!form) return false;

    const requiredFields = form.querySelectorAll('[required]');
    let isValid = true;

    requiredFields.forEach(field => {
        if (!field.value.trim()) {
            isValid = false;
            field.style.borderColor = '#ff4d4f';
        } else {
            field.style.borderColor = '#d9d9d9';
        }
    });

    if (!isValid) {
        utils.showMessage('请填写所有必填项', 'error');
    }

    return isValid;
}

// 表格排序
function sortTable(tableId, columnIndex) {
    const table = document.getElementById(tableId);
    if (!table) return;

    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));

    rows.sort((a, b) => {
        const aValue = a.cells[columnIndex].textContent.trim();
        const bValue = b.cells[columnIndex].textContent.trim();
        return aValue.localeCompare(bValue, 'zh-CN');
    });

    rows.forEach(row => tbody.appendChild(row));
}

// 导出为CSV
function exportTableToCSV(tableId, filename = 'export.csv') {
    const table = document.getElementById(tableId);
    if (!table) return;

    let csv = [];
    const rows = table.querySelectorAll('tr');

    rows.forEach(row => {
        const cols = row.querySelectorAll('td, th');
        const rowData = Array.from(cols).map(col => {
            return '"' + col.textContent.trim().replace(/"/g, '""') + '"';
        });
        csv.push(rowData.join(','));
    });

    const csvContent = csv.join('\n');
    const blob = new Blob(['\ufeff' + csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);

    link.setAttribute('href', url);
    link.setAttribute('download', filename);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
}

// 打印
function printPage() {
    window.print();
}

// 页面加载完成后执行
document.addEventListener('DOMContentLoaded', function() {
    window.AppUI = AppUI;

    // Bootstrap风格下拉框统一
    document.querySelectorAll('select.form-control').forEach((el) => {
        el.classList.remove('form-control');
        el.classList.add('form-select');
    });

    // 不挂载 Vue 到整个 #app，避免重建 DOM 导致页面事件绑定丢失
    const cached = localStorage.getItem('zs_sidebar_collapsed');
    if (cached === '1' && window.innerWidth > 768) {
        document.body.classList.add('sidebar-collapsed');
    }
    const toggle = document.getElementById('sidebarToggle');
    if (toggle) {
        toggle.addEventListener('click', () => {
            if (window.innerWidth <= 768) {
                document.body.classList.toggle('sidebar-mobile-open');
                return;
            }
            document.body.classList.toggle('sidebar-collapsed');
            localStorage.setItem(
                'zs_sidebar_collapsed',
                document.body.classList.contains('sidebar-collapsed') ? '1' : '0'
            );
        });
    }
    window.addEventListener('resize', () => {
        if (window.innerWidth > 768) {
            document.body.classList.remove('sidebar-mobile-open');
        }
    });

    // 自动消失的消息提示
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        if (alert.classList.contains('alert-error')) {
            alert.classList.add('alert-danger');
        }
        setTimeout(() => {
            alert.style.transition = 'opacity 0.3s';
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 300);
        }, 3000);
    });

    // 表单提交前验证
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            const submitter = e.submitter;
            if (submitter && submitter.id === 'parseReceiverBtn') {
                e.preventDefault();
                return;
            }
            const requiredFields = form.querySelectorAll('[required]');
            let isValid = true;

            requiredFields.forEach(field => {
                if (!field.value.trim()) {
                    isValid = false;
                    field.style.borderColor = '#ff4d4f';
                } else {
                    field.style.borderColor = '#d9d9d9';
                }
            });

            if (!isValid) {
                e.preventDefault();
                utils.showMessage('请填写所有必填项', 'error');
            }
        });
    });

    // 输入框焦点效果
    const inputs = document.querySelectorAll('.form-control, .form-select');
    inputs.forEach(input => {
        input.addEventListener('focus', function() {
            this.parentElement.classList.add('focused');
        });

        input.addEventListener('blur', function() {
            this.parentElement.classList.remove('focused');
        });
    });
});

// 导出工具函数
window.utils = utils;
window.validateForm = validateForm;
window.sortTable = sortTable;
window.exportTableToCSV = exportTableToCSV;
window.printPage = printPage;
window.AppUI = AppUI;
