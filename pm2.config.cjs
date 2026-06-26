# PM2 进程守护配置
# 用法:
#   npm install -g pm2   (或 nvm 环境)
#   pm2 start pm2.config.cjs
#   pm2 save              # 保存进程列表，重启后自动恢复
#   pm2 startup           # 设置开机自启

module.exports = {
  apps: [{
    name: "xpu-monitor",
    script: "proxy.js",
    cwd: __dirname,
    interpreter: "node",
    env: {
      NODE_ENV: "production",
      // 以下可选，不设则使用 config.json 默认值
      // PROXY_PORT: "8900",
      // LOCKBOT_HOST: "10.206.192.17",
      // LOCKBOT_PORT: "8875",
      // MONQUERY_HOST: "api.mt.noah.baidu.com",
      // MONQUERY_PORT: "8557",
    },
    // 自动重启
    autorestart: true,
    max_restarts: 10,
    restart_delay: 5000,
    // 日志
    log_date_format: "YYYY-MM-DD HH:mm:ss",
    error_file: "logs/pm2-error.log",
    out_file: "logs/pm2-out.log",
    merge_logs: true,
  }],
};
