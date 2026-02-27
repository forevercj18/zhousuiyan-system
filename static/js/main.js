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
        const alertClass = `alert-${type}`;
        const alertHtml = `
            <div class="alert ${alertClass}">
                ${message}
            </div>
        `;

        const messagesContainer = document.querySelector('.messages') || createMessagesContainer();
        messagesContainer.insertAdjacentHTML('beforeend', alertHtml);

        // 3秒后自动消失
        setTimeout(() => {
            const alert = messagesContainer.lastElementChild;
            if (alert) {
                alert.style.transition = 'opacity 0.3s';
                alert.style.opacity = '0';
                setTimeout(() => alert.remove(), 300);
            }
        }, 3000);
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
    // 自动消失的消息提示
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
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
    const inputs = document.querySelectorAll('.form-control');
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
