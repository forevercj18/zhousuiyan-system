// 日历相关功能

// 日历事件处理已在calendar.html中实现
// 这里添加一些辅助功能

// 跳转到今天
function goToToday() {
    currentDate = new Date();
    renderCalendar();
}

// 跳转到指定日期
function goToDate(dateStr) {
    currentDate = new Date(dateStr);
    renderCalendar();
}

// 导出日历数据
function exportCalendar() {
    const year = currentDate.getFullYear();
    const month = currentDate.getMonth() + 1;
    const filename = `calendar_${year}_${month}.csv`;

    const monthEvents = events.filter(e => {
        const eventDate = new Date(e.start);
        return eventDate.getFullYear() === year && eventDate.getMonth() === month - 1;
    });

    if (monthEvents.length === 0) {
        utils.showMessage('本月暂无活动数据', 'warning');
        return;
    }

    let csv = ['日期,客户信息,订单号,状态'];
    monthEvents.forEach(e => {
        const statusText = e.status === 'confirmed' ? '已确认' : '已送达';
        csv.push(`${e.start},${e.title},${e.order_no},${statusText}`);
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

    utils.showMessage('日历数据导出成功', 'success');
}

// 打印日历
function printCalendar() {
    window.print();
}

// 切换视图模式
let viewMode = 'month'; // month, week, day

function switchView(mode) {
    viewMode = mode;
    // 实现不同视图模式的渲染
    console.log('Switch to view:', mode);
}

// 添加快捷键支持
document.addEventListener('keydown', function(e) {
    // 左箭头：上月
    if (e.key === 'ArrowLeft') {
        document.getElementById('prevMonth')?.click();
    }
    // 右箭头：下月
    if (e.key === 'ArrowRight') {
        document.getElementById('nextMonth')?.click();
    }
    // T键：回到今天
    if (e.key === 't' || e.key === 'T') {
        goToToday();
    }
});

// 日历数据刷新
function refreshCalendar() {
    utils.showMessage('正在刷新日历...', 'info');
    // 实现数据刷新逻辑
    setTimeout(() => {
        renderCalendar();
        utils.showMessage('日历已刷新', 'success');
    }, 500);
}
