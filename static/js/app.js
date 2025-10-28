// 全局变量
let profitChart = null;
let signalChart = null;
let confidenceChart = null;

// 初始化
document.addEventListener('DOMContentLoaded', async function() {
    initCharts();
    updateData();
    updateMultiOverview();
    try {
        const cfgResp = await fetch('/api/config');
        const cfg = await cfgResp.json();
        const intervalMs = (cfg && cfg.frontend_refresh_interval_ms) ? cfg.frontend_refresh_interval_ms : 1000;
        // 每配置的间隔更新一次数据（实时显示价格和持仓）
        setInterval(updateData, intervalMs);
        setInterval(updateMultiOverview, Math.max(2000, intervalMs * 2));
    } catch (e) {
        // 回退到1秒
        setInterval(updateData, 1000);
        setInterval(updateMultiOverview, 2000);
    }
});

// 初始化图表
function initCharts() {
    // 收益曲线图
    profitChart = echarts.init(document.getElementById('profitChart'));
    
    // 信号分布图
    signalChart = echarts.init(document.getElementById('signalChart'));
    
    // 信心分布图
    confidenceChart = echarts.init(document.getElementById('confidenceChart'));
    
    // 响应窗口大小变化
    window.addEventListener('resize', function() {
        profitChart.resize();
        signalChart.resize();
        confidenceChart.resize();
    });
}

// 更新所有数据
async function updateData() {
    try {
        // 更新AI模型信息
        await updateAIModelInfo();
        
        // 更新仪表板数据
        await updateDashboard();
        
        // 更新收益曲线图
        await updateProfitChart();

        // 更新聚合绩效(历史+当前)
        await updatePerformanceAggregate();
        
        // 更新AI决策
        await updateAIDecisions();
        
        // 更新交易记录
        await updateTrades();
        
        // 更新信号统计
        await updateSignalStats();
        
    } catch (error) {
        console.error('数据更新失败:', error);
    }
}
async function updatePerformanceAggregate() {
    try {
        const resp = await fetch('/api/performance');
        const perf = await resp.json();
        const totalProfitEl = document.getElementById('totalProfit');
        if (totalProfitEl && typeof perf.realized_profit_usdt === 'number') {
            // 这里沿用“总盈亏=已实现+未实现”的展示，未实现由 dashboard 已显示；此处强调已实现部分
            totalProfitEl.title = `已实现盈亏(历史): $${perf.realized_profit_usdt.toFixed(2)}`;
        }
        const winRateEl = document.getElementById('winRate');
        if (winRateEl && typeof perf.win_rate === 'number') {
            winRateEl.textContent = `${perf.win_rate.toFixed(1)}%`;
        }
        const totalTradesEl = document.getElementById('totalTrades');
        if (totalTradesEl && typeof perf.total_trades === 'number') {
            totalTradesEl.textContent = String(perf.total_trades);
        }
    } catch (e) {
        console.error('聚合绩效更新失败:', e);
    }
}

// 更新AI模型信息
async function updateAIModelInfo() {
    try {
        const response = await fetch('/api/ai_model_info');
        const data = await response.json();
        
        // 更新模型名称
        const modelNameMap = {
            'deepseek': 'DeepSeek',
            'qwen': '阿里百炼 Qwen'
        };
        const modelName = modelNameMap[data.provider] || data.provider;
        document.getElementById('aiModelName').textContent = `${modelName} (${data.model})`;
        
        // 更新连接状态
        const statusDot = document.getElementById('aiStatusDot');
        const statusText = document.getElementById('aiStatusText');
        
        // 清除旧的状态类
        statusDot.className = 'status-dot';
        
        // 设置新的状态
        if (data.status === 'connected') {
            statusDot.classList.add('connected');
            statusText.textContent = '已连接';
            statusText.style.color = 'var(--success-color)';
        } else if (data.status === 'error') {
            statusDot.classList.add('error');
            const codeText = (data.error_code !== undefined && data.error_code !== null) ? String(data.error_code) : 'N/A';
            const typeText = data.error_type || 'Error';
            // 在状态文本中直接体现报错码/类型
            statusText.textContent = codeText !== 'N/A' ? `连接失败 (${typeText} ${codeText})` : `连接失败 (${typeText})`;
            statusText.style.color = 'var(--danger-color)';
            // 详细信息放入提示（tooltip）
            const tooltipParts = [];
            if (data.error_message) tooltipParts.push(data.error_message);
            tooltipParts.push(`类型: ${typeText}`);
            tooltipParts.push(`代码: ${codeText}`);
            statusText.title = tooltipParts.join(' | ');
        } else {
            statusDot.classList.add('unknown');
            statusText.textContent = '检测中';
            statusText.style.color = 'var(--warning-color)';
        }
        
    } catch (error) {
        console.error('AI模型信息更新失败:', error);
        document.getElementById('aiModelName').textContent = 'AI模型加载失败';
        document.getElementById('aiStatusText').textContent = '错误';
    }
}

// 更新仪表板
async function updateDashboard() {
    try {
        const response = await fetch('/api/dashboard');
        const data = await response.json();
        
        // 账户信息
        document.getElementById('usdtBalance').textContent = 
            data.account_info?.usdt_balance ? `$${data.account_info.usdt_balance.toFixed(2)}` : '--';
        document.getElementById('totalEquity').textContent = 
            data.account_info?.total_equity ? `$${data.account_info.total_equity.toFixed(2)}` : '--';
        
        // 配置信息
        document.getElementById('leverage').textContent = 
            data.config?.leverage ? `${data.config.leverage}x` : '--';
        document.getElementById('timeframe').textContent = 
            data.config?.timeframe || '--';
        document.getElementById('tradeMode').textContent = 
            data.config?.test_mode ? '模拟模式' : '实盘模式';
        // 头部 symbol
        const sym = data.config?.symbol || '--';
        const head = document.getElementById('symbolHeader');
        if (head) head.textContent = sym;
        
        // 当前价格
        if (data.current_price) {
            document.getElementById('currentPrice').textContent = 
                `$${data.current_price.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
        }
        
        // 持仓信息
        if (data.current_position) {
            const pos = data.current_position;
            const posType = document.getElementById('positionType');
            posType.textContent = pos.side === 'long' ? '多头持仓' : '空头持仓';
            posType.className = `position-type ${pos.side}`;
            
            document.getElementById('positionSize').textContent = `${pos.size} 张`;
            document.getElementById('entryPrice').textContent = `$${pos.entry_price.toFixed(2)}`;
            
            const pnlElement = document.getElementById('unrealizedPnl');
            pnlElement.textContent = `$${pos.unrealized_pnl.toFixed(2)}`;
            pnlElement.className = `value pnl ${pos.unrealized_pnl >= 0 ? 'positive' : 'negative'}`;
        } else {
            document.getElementById('positionType').textContent = '无持仓';
            document.getElementById('positionType').className = 'position-type';
            document.getElementById('positionSize').textContent = '--';
            document.getElementById('entryPrice').textContent = '--';
            document.getElementById('unrealizedPnl').textContent = '--';
        }
        
        // 绩效统计（总盈亏=已实现+未实现）
        const totalProfitEl = document.getElementById('totalProfit');
        const realized = (typeof data.realized_profit_usdt === 'number') ? data.realized_profit_usdt : 0;
        let unrealized = 0;
        if (data.current_position && typeof data.current_position.unrealized_pnl === 'number') {
            unrealized = data.current_position.unrealized_pnl;
        }
        const totalPnl = realized + unrealized;
        totalProfitEl.textContent = `$${totalPnl.toFixed(2)}`;
        totalProfitEl.className = `value pnl ${totalPnl >= 0 ? 'positive' : 'negative'}`;
        
        document.getElementById('winRate').textContent = 
            data.performance?.win_rate ? `${data.performance.win_rate.toFixed(1)}%` : '--';
        document.getElementById('totalTrades').textContent = 
            data.performance?.total_trades || '0';
        
        // 更新时间
        document.getElementById('lastUpdate').textContent = 
            data.last_update || '--';
            
    } catch (error) {
        console.error('仪表板更新失败:', error);
    }
}

// 更新K线图
async function updateKlineChart() {
    try {
        const response = await fetch('/api/kline');
        const data = await response.json();
        
        if (!data || data.length === 0) return;
        
        // 准备K线数据
        const dates = [];
        const klineData = [];
        const volumes = [];
        
        data.forEach(item => {
            const date = new Date(item.timestamp);
            dates.push(date.toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'}));
            klineData.push([item.open, item.close, item.low, item.high]);
            volumes.push(item.volume);
        });
        
        const option = {
            backgroundColor: 'transparent',
            tooltip: {
                trigger: 'axis',
                axisPointer: {
                    type: 'cross'
                },
                backgroundColor: 'rgba(26, 26, 46, 0.95)',
                borderColor: '#2d2d44',
                textStyle: {
                    color: '#fff'
                }
            },
            legend: {
                data: ['K线', '成交量'],
                textStyle: {
                    color: '#9ca3af'
                }
            },
            grid: [
                {
                    left: '10%',
                    right: '10%',
                    top: '10%',
                    height: '60%'
                },
                {
                    left: '10%',
                    right: '10%',
                    top: '75%',
                    height: '15%'
                }
            ],
            xAxis: [
                {
                    type: 'category',
                    data: dates,
                    scale: true,
                    boundaryGap: false,
                    axisLine: { lineStyle: { color: '#2d2d44' } },
                    axisLabel: { color: '#9ca3af' },
                    splitLine: { show: false },
                    min: 'dataMin',
                    max: 'dataMax'
                },
                {
                    type: 'category',
                    gridIndex: 1,
                    data: dates,
                    scale: true,
                    boundaryGap: false,
                    axisLine: { lineStyle: { color: '#2d2d44' } },
                    axisLabel: { show: false },
                    splitLine: { show: false }
                }
            ],
            yAxis: [
                {
                    scale: true,
                    splitArea: { show: false },
                    axisLine: { lineStyle: { color: '#2d2d44' } },
                    axisLabel: { color: '#9ca3af' },
                    splitLine: { lineStyle: { color: '#2d2d44' } }
                },
                {
                    scale: true,
                    gridIndex: 1,
                    splitNumber: 2,
                    axisLabel: { show: false },
                    axisLine: { show: false },
                    splitLine: { show: false }
                }
            ],
            dataZoom: [
                {
                    type: 'inside',
                    xAxisIndex: [0, 1],
                    start: 50,
                    end: 100
                },
                {
                    show: true,
                    xAxisIndex: [0, 1],
                    type: 'slider',
                    bottom: '5%',
                    start: 50,
                    end: 100,
                    backgroundColor: '#1a1a2e',
                    fillerColor: 'rgba(26, 115, 232, 0.2)',
                    borderColor: '#2d2d44',
                    textStyle: { color: '#9ca3af' }
                }
            ],
            series: [
                {
                    name: 'K线',
                    type: 'candlestick',
                    data: klineData,
                    itemStyle: {
                        color: '#34a853',
                        color0: '#ea4335',
                        borderColor: '#34a853',
                        borderColor0: '#ea4335'
                    }
                },
                {
                    name: '成交量',
                    type: 'bar',
                    xAxisIndex: 1,
                    yAxisIndex: 1,
                    data: volumes,
                    itemStyle: {
                        color: function(params) {
                            const kline = klineData[params.dataIndex];
                            return kline[1] > kline[0] ? '#34a853' : '#ea4335';
                        }
                    }
                }
            ]
        };
        
        klineChart.setOption(option);
        
    } catch (error) {
        console.error('K线图更新失败:', error);
    }
}

// 更新收益曲线图
async function updateProfitChart() {
    try {
        const response = await fetch('/api/profit_curve');
        const data = await response.json();
        
        if (!data || data.length === 0) {
            const option = {
                backgroundColor: 'transparent',
                title: {
                    text: '暂无收益数据，等待交易执行...',
                    left: 'center',
                    top: 'center',
                    textStyle: { color: '#9ca3af', fontSize: 18 }
                }
            };
            profitChart.setOption(option);
            return;
        }
        
        const timestamps = [], profitRates = [], profits = [], equities = [];
        
        data.forEach(item => {
            const date = new Date(item.timestamp);
            timestamps.push(date.toLocaleString('zh-CN', {
                month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
            }));
            profitRates.push(item.profit_rate.toFixed(2));
            profits.push(item.profit.toFixed(2));
            equities.push(item.equity.toFixed(2));
        });
        
        const option = {
            backgroundColor: 'transparent',
            tooltip: {
                trigger: 'axis',
                axisPointer: { type: 'cross', label: { backgroundColor: '#6a7985' } },
                backgroundColor: 'rgba(26, 26, 46, 0.95)',
                borderColor: '#2d2d44',
                textStyle: { color: '#fff' },
                formatter: function(params) {
                    let result = params[0].name + '<br/>';
                    params.forEach(param => {
                        result += '<span style="display:inline-block;margin-right:5px;border-radius:10px;width:10px;height:10px;background-color:' + param.color + ';"></span>';
                        result += param.seriesName + ': ' + param.value;
                        result += (param.seriesName === '收益率') ? '%<br/>' : ' USDT<br/>';
                    });
                    return result;
                }
            },
            legend: {
                data: ['收益率', '累计盈亏', '账户权益'],
                textStyle: { color: '#9ca3af' },
                top: '5%'
            },
            grid: { left: '3%', right: '4%', bottom: '15%', top: '15%', containLabel: true },
            xAxis: {
                type: 'category',
                boundaryGap: false,
                data: timestamps,
                axisLine: { lineStyle: { color: '#2d2d44' } },
                axisLabel: { color: '#9ca3af', rotate: 45 },
                splitLine: { show: true, lineStyle: { color: '#2d2d44' } }
            },
            yAxis: [
                {
                    type: 'value',
                    name: '收益率(%)',
                    position: 'left',
                    axisLine: { lineStyle: { color: '#2d2d44' } },
                    axisLabel: { color: '#9ca3af', formatter: '{value}%' },
                    splitLine: { lineStyle: { color: '#2d2d44' } }
                },
                {
                    type: 'value',
                    name: '金额(USDT)',
                    position: 'right',
                    axisLine: { lineStyle: { color: '#2d2d44' } },
                    axisLabel: { color: '#9ca3af' },
                    splitLine: { show: false }
                }
            ],
            dataZoom: [
                { type: 'inside', start: 0, end: 100 },
                { show: true, type: 'slider', bottom: '5%', start: 0, end: 100,
                  backgroundColor: '#1a1a2e', fillerColor: 'rgba(26, 115, 232, 0.2)',
                  borderColor: '#2d2d44', textStyle: { color: '#9ca3af' } }
            ],
            series: [
                {
                    name: '收益率',
                    type: 'line',
                    yAxisIndex: 0,
                    data: profitRates,
                    smooth: true,
                    lineStyle: { width: 3, color: { type: 'linear', x: 0, y: 0, x2: 1, y2: 0,
                        colorStops: [{ offset: 0, color: '#1a73e8' }, { offset: 1, color: '#34a853' }] } },
                    areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [{ offset: 0, color: 'rgba(26, 115, 232, 0.3)' }, { offset: 1, color: 'rgba(26, 115, 232, 0.05)' }] } },
                    markLine: { silent: true, symbol: 'none', lineStyle: { color: '#9ca3af', type: 'dashed' },
                        data: [{ yAxis: 0, label: { formatter: '盈亏平衡线', position: 'end' } }] }
                },
                {
                    name: '累计盈亏',
                    type: 'line',
                    yAxisIndex: 1,
                    data: profits,
                    smooth: true,
                    lineStyle: { width: 2, color: '#fbbc04' },
                    itemStyle: { color: '#fbbc04' }
                },
                {
                    name: '账户权益',
                    type: 'line',
                    yAxisIndex: 1,
                    data: equities,
                    smooth: true,
                    lineStyle: { width: 2, color: '#34a853', type: 'dashed' },
                    itemStyle: { color: '#34a853' }
                }
            ]
        };
        
        profitChart.setOption(option);
        
    } catch (error) {
        console.error('收益曲线图更新失败:', error);
    }
}

// 更新AI决策
async function updateAIDecisions() {
    try {
        const response = await fetch('/api/ai_decisions?limit=100');
        const data = await response.json();
        
        if (!data || data.length === 0) return;
        
        // 显示最新决策
        const latest = data[data.length - 1];
        const latestDiv = document.getElementById('latestDecision');
        
        latestDiv.innerHTML = `
            <div class="ai-signal">
                <span class="signal-badge ${latest.signal}">${latest.signal}</span>
                <span class="confidence-badge ${latest.confidence}">${latest.confidence}</span>
            </div>
            <div class="ai-reason">${latest.reason}</div>
            <div class="ai-prices">
                <span class="stop-loss">止损: $${latest.stop_loss.toFixed(2)}</span>
                <span class="take-profit">止盈: $${latest.take_profit.toFixed(2)}</span>
            </div>
            <div style="color: #9ca3af; font-size: 0.85em; margin-top: 10px;">
                ${latest.timestamp}
            </div>
        `;
        
        // 显示历史决策
        const historyDiv = document.getElementById('aiHistory');
        const historyHTML = data.slice(0, Math.max(0, data.length - 1)).reverse().map(decision => `
            <div class="ai-history-item">
                <div style="display: flex; justify-content: space-between; margin-bottom: 5px;">
                    <span class="signal-badge ${decision.signal}" style="font-size: 0.8em; padding: 4px 10px;">${decision.signal}</span>
                    <span style="color: #9ca3af; font-size: 0.8em;">${decision.timestamp}</span>
                </div>
                <div style="color: #9ca3af;">${decision.reason}</div>
            </div>
        `).join('');
        
        const wasAtBottom = Math.abs(historyDiv.scrollHeight - historyDiv.clientHeight - historyDiv.scrollTop) < 5;
        historyDiv.innerHTML = historyHTML || '<div style="color: #9ca3af; text-align: center; padding: 20px;">暂无历史记录</div>';
        // 自动滚动到底部（仅在之前已在底部时）
        if (wasAtBottom) {
            historyDiv.scrollTop = historyDiv.scrollHeight;
        }
        
    } catch (error) {
        console.error('AI决策更新失败:', error);
    }
}

// 更新交易记录
async function updateTrades() {
    try {
        const response = await fetch('/api/trades');
        const data = await response.json();
        
        const tbody = document.getElementById('tradesBody');
        
        if (!data || data.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="no-data">暂无交易记录</td></tr>';
            return;
        }
        
        const rows = data.slice(-20).reverse().map(trade => `
            <tr>
                <td>${trade.timestamp}${trade.symbol ? ` • ${trade.symbol}` : ''}</td>
                <td><span class="signal-badge ${trade.signal}" style="font-size: 0.8em; padding: 4px 10px;">${trade.signal}</span></td>
                <td>$${trade.price.toFixed(2)}</td>
                <td>${trade.amount} ${trade.base || ''}</td>
                <td><span class="confidence-badge ${trade.confidence}" style="font-size: 0.75em; padding: 3px 8px;">${trade.confidence}</span></td>
                <td style="max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${trade.reason}">${trade.reason}</td>
            </tr>
        `).join('');
        
        tbody.innerHTML = rows;
        
    } catch (error) {
        console.error('交易记录更新失败:', error);
    }
}
// 多交易对总览与操作
async function updateMultiOverview() {
    try {
        const resp = await fetch('/api/multi/overview');
        const json = await resp.json();
        const tbody = document.getElementById('multiOverviewBody');
        if (!tbody) return;
        const items = json?.items || [];
        if (!items.length) {
            tbody.innerHTML = '<tr><td colspan="9" class="no-data">暂无数据</td></tr>';
            return;
        }
        const rows = items.map(it => {
            const pos = it.position;
            const sideText = pos ? (pos.side === 'long' ? '多头' : '空头') : '无';
            const sizeText = pos?.size || '--';
            const entryText = pos?.entry_price ? `$${Number(pos.entry_price).toFixed(2)}` : '--';
            const pnlText = pos?.unrealized_pnl !== undefined ? `$${Number(pos.unrealized_pnl).toFixed(2)}` : '--';
            const tpDefault = (it.tpsl_expected && it.tpsl_expected.tp != null) ? it.tpsl_expected.tp : '';
            const slDefault = (it.tpsl_expected && it.tpsl_expected.sl != null) ? it.tpsl_expected.sl : '';
            return `
            <tr>
                <td>${it.symbol}</td>
                <td>${it.price ? `$${Number(it.price).toFixed(2)}` : '--'}</td>
                <td>${sideText}</td>
                <td>${sizeText}</td>
                <td>${entryText}</td>
                <td>${pnlText}</td>
                <td>
                    <input type="number" step="0.01" placeholder="TP" style="width: 90px;" data-tp-for="${it.symbol}" value="${tpDefault}">
                </td>
                <td>
                    <input type="number" step="0.01" placeholder="SL" style="width: 90px;" data-sl-for="${it.symbol}" value="${slDefault}">
                </td>
                <td>
                    <button onclick="submitTPSL('${it.symbol}')">设置TP/SL</button>
                    <button onclick="closePosition('${it.symbol}')">平仓</button>
                </td>
            </tr>`;
        }).join('');
        tbody.innerHTML = rows;
    } catch (e) {
        console.error('多合约总览更新失败:', e);
    }
}

async function submitTPSL(symbol) {
    try {
        const tpInput = document.querySelector(`input[data-tp-for="${symbol}"]`);
        const slInput = document.querySelector(`input[data-sl-for="${symbol}"]`);
        const body = {
            symbol,
            take_profit: tpInput && tpInput.value ? Number(tpInput.value) : null,
            stop_loss: slInput && slInput.value ? Number(slInput.value) : null
        };
        const resp = await fetch('/api/position/tpsl', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const json = await resp.json();
        if (!json.ok) throw new Error(json.error || '设置失败');
        alert('已提交TP/SL设置');
    } catch (e) {
        alert('设置失败: ' + e.message);
    }
}

async function closePosition(symbol) {
    try {
        const resp = await fetch('/api/position/close', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol }) });
        const json = await resp.json();
        if (!json.ok) throw new Error(json.error || '平仓失败');
        alert('已提交平仓订单');
    } catch (e) {
        alert('平仓失败: ' + e.message);
    }
}


// 更新信号统计
async function updateSignalStats() {
    try {
        const response = await fetch('/api/signals');
        const data = await response.json();
        
        if (!data) return;
        
        // 信号分布饼图
        const signalOption = {
            backgroundColor: 'transparent',
            title: {
                text: '信号分布',
                left: 'center',
                textStyle: {
                    color: '#9ca3af',
                    fontSize: 16
                }
            },
            tooltip: {
                trigger: 'item',
                backgroundColor: 'rgba(26, 26, 46, 0.95)',
                borderColor: '#2d2d44',
                textStyle: { color: '#fff' }
            },
            legend: {
                orient: 'vertical',
                left: 'left',
                textStyle: { color: '#9ca3af' }
            },
            series: [
                {
                    type: 'pie',
                    radius: '50%',
                    data: [
                        { value: data.signal_stats.BUY || 0, name: 'BUY', itemStyle: { color: '#34a853' } },
                        { value: data.signal_stats.SELL || 0, name: 'SELL', itemStyle: { color: '#ea4335' } },
                        { value: data.signal_stats.HOLD || 0, name: 'HOLD', itemStyle: { color: '#fbbc04' } }
                    ],
                    emphasis: {
                        itemStyle: {
                            shadowBlur: 10,
                            shadowOffsetX: 0,
                            shadowColor: 'rgba(0, 0, 0, 0.5)'
                        }
                    }
                }
            ]
        };
        
        signalChart.setOption(signalOption);
        
        // 信心分布饼图
        const confidenceOption = {
            backgroundColor: 'transparent',
            title: {
                text: '信心分布',
                left: 'center',
                textStyle: {
                    color: '#9ca3af',
                    fontSize: 16
                }
            },
            tooltip: {
                trigger: 'item',
                backgroundColor: 'rgba(26, 26, 46, 0.95)',
                borderColor: '#2d2d44',
                textStyle: { color: '#fff' }
            },
            legend: {
                orient: 'vertical',
                left: 'left',
                textStyle: { color: '#9ca3af' }
            },
            series: [
                {
                    type: 'pie',
                    radius: '50%',
                    data: [
                        { value: data.confidence_stats.HIGH || 0, name: 'HIGH', itemStyle: { color: '#34a853' } },
                        { value: data.confidence_stats.MEDIUM || 0, name: 'MEDIUM', itemStyle: { color: '#fbbc04' } },
                        { value: data.confidence_stats.LOW || 0, name: 'LOW', itemStyle: { color: '#ea4335' } }
                    ],
                    emphasis: {
                        itemStyle: {
                            shadowBlur: 10,
                            shadowOffsetX: 0,
                            shadowColor: 'rgba(0, 0, 0, 0.5)'
                        }
                    }
                }
            ]
        };
        
        confidenceChart.setOption(confidenceOption);
        
    } catch (error) {
        console.error('信号统计更新失败:', error);
    }
}

