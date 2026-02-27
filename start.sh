#!/bin/bash

echo "=========================================="
echo "宝宝周岁宴道具租赁系统 - 快速启动脚本"
echo "=========================================="
echo ""

# 检查Python版本
echo "检查Python版本..."
python3 --version

# 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
echo "激活虚拟环境..."
source venv/bin/activate

# 安装依赖
echo "安装依赖包..."
pip install -r requirements.txt

# 数据库迁移
echo "执行数据库迁移..."
python manage.py migrate

# 创建超级用户
echo "创建超级用户..."
python scripts/create_superuser.py

# 收集静态文件
echo "收集静态文件..."
python manage.py collectstatic --noinput

echo ""
echo "=========================================="
echo "系统启动完成！"
echo "=========================================="
echo ""
echo "访问地址: http://localhost:8000"
echo "默认账号: admin"
echo "默认密码: admin123"
echo ""
echo "正在启动开发服务器..."
echo ""

# 启动服务器
python manage.py runserver
