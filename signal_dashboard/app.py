# -*- coding: utf-8 -*-
"""
MA策略信号分析平台 v1.1.0
功能：信号计算、分类、MiniMax新闻搜索、M2.7 AI分析、评分排名、历史查询
"""
import os
import sys
import json
import sqlite3
import time
import numpy as np
import requests as http_requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, send_file, Response
from _fetch_indicators import _fetch_indicators
from flask_cors import CORS
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# MiniMax MCP 工具
sys.path.insert(0, r'C:\Users\12434\.agents\skills\minimax-mcp')
from minimax_mcp_tools import minimax_web_search

# Tavily 搜索（备用）
TAVILY_API_KEY = os.environ.get('TAVILY_API_KEY', '')

# MiniMax M2.7 API
MINIMAX_API_KEY = os.environ.get('MINIMAX_API_KEY', '')
MINIMAX_API_URL = os.environ.get('MINIMAX_API_URL', 'https://api.minimaxi.com/anthropic/v1/messages')

# 全局中断标志
_analysis_aborted = {}  # run_id -> True/False

# 添加父目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = r'D:\yxw\PY\astock_data\astock_data.db'
ANALYSIS_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'analysis_history.db')

app = Flask(__name__)
CORS(app)

# ==================== 数据库初始化 ====================
def init_analysis_db():
    conn = sqlite3.connect(ANALYSIS_DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS analysis_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_date TEXT NOT NULL,
        signal_date TEXT NOT NULL,
        created_at TEXT NOT NULL,
        stock_count INTEGER,
        summary TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS analysis_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL,
        seq INTEGER DEFAULT 0,
        code TEXT NOT NULL,
        name TEXT,
        signal_type TEXT,
        grade TEXT,
        score INTEGER,
        analysis TEXT,
        news_links TEXT,
        fundamentals TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES analysis_runs(id)
    )''')
    conn.commit()
    # Migration: add seq column if not exists (only runs if table already existed)
    try:
        c.execute("SELECT seq FROM analysis_results WHERE 1=0")
    except:
        pass  # column doesn't exist
    else:
        try:
            c.execute("ALTER TABLE analysis_results ADD COLUMN seq INTEGER DEFAULT 0")
            conn.commit()
        except:
            pass
    conn.close()

init_analysis_db()

# ==================== 信号计算（复用原逻辑）====================
def get_available_dates():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT DISTINCT date FROM stock_daily WHERE date >= date('now','-90 days') ORDER BY date DESC LIMIT 60")
    dates = [r[0] for r in c.fetchall()]
    conn.close()
    return dates

def _ema(data, span):
    prices = list(reversed(data))
    multiplier = 2 / (span + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = (p - ema) * multiplier + ema
    return ema

def _rsi(prices, period=14):
    prices_asc = list(reversed(prices))
    deltas = [prices_asc[i] - prices_asc[i-1] for i in range(1, len(prices_asc))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    if len(gains) < period:
        return 50
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))

def calc_signals_for_date(scan_date: str) -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"SELECT COUNT(*) FROM stock_daily WHERE date = '{scan_date}'")
    if c.fetchone()[0] == 0:
        conn.close()
        return []

    c.execute("SELECT code,name,date,open,high,low,close,volume FROM stock_daily WHERE date=? AND close IS NOT NULL ORDER BY code", (scan_date,))
    rows = c.fetchall()
    signals = []

    for code, name, date, open_p, high, low, close, volume in rows:
        if close is None or volume is None:
            continue
        c2 = conn.cursor()
        c2.execute("SELECT close,volume FROM stock_daily WHERE code=? AND date<=? AND close IS NOT NULL ORDER BY date DESC LIMIT 60", (code, scan_date))
        hist = c2.fetchall()
        if len(hist) < 20:
            continue

        prices = [r[0] for r in hist]
        volumes = [r[1] for r in hist]
        ma5 = np.mean(prices[:5])
        ma10 = np.mean(prices[:10])
        ma20 = np.mean(prices[:20])
        vol5 = np.mean(volumes[:5])
        vol60 = np.mean(volumes[4:7]) if len(volumes) >= 7 else np.mean(volumes[1:])
        vol_ratio = volume / vol60 if vol60 > 0 else 0
        vol_ratio_5 = volume / vol5 if vol5 > 0 else 0

        above_ma5_today = close > ma5
        below_ma5_3days = all(prices[i] < ma5 for i in range(1, min(4, len(prices))))
        rise_today = (close - prices[1]) / prices[1] * 100 if len(prices) > 1 else 0
        dist_ma20 = (close - ma20) / ma20 * 100 if ma20 > 0 else 0
        dist_ma5 = (close - ma5) / ma5 * 100 if ma5 > 0 else 0
        rsi = _rsi(prices, 14) if len(prices) >= 15 else 50

        signal_type = None
        grade = None

        if above_ma5_today and below_ma5_3days:
            if rise_today > 5 or code.startswith(('688', '889', '873')):
                pass
            elif vol_ratio >= 2.3:
                if rise_today <= 2 and dist_ma20 > 0:
                    grade = 'A'
                elif rise_today <= 2:
                    grade = 'B'
                elif rise_today <= 5:
                    grade = 'B'
                else:
                    grade = 'C'
                signal_type = '回踩MA5'
            elif vol_ratio >= 1.3:
                grade = 'B' if (rise_today <= 2 and dist_ma20 > 0) else 'C'
                signal_type = '回踩MA5'

        if signal_type is None and len(prices) >= 20:
            high_20 = max(prices[:20])
            if close >= high_20 and vol_ratio >= 2.3 and not code.startswith(('688', '889', '873')):
                signal_type = '放量突破'
                grade = 'C' if dist_ma20 > 5 else 'B'

        if signal_type is None and len(prices) >= 5:
            pct_5 = (close - prices[4]) / prices[4] * 100 if prices[4] > 0 else 0
            if pct_5 < -7 and close > ma5 and close > open_p and not code.startswith(('688', '889', '873')):
                signal_type = '强势低吸'
                grade = 'D'

        if signal_type and grade:
            signals.append({
                'code': code, 'name': name or '',
                'close': round(close, 2), 'open': round(open_p, 2),
                'high': round(high, 2), 'low': round(low, 2),
                'ma5': round(ma5, 2), 'ma10': round(ma10, 2), 'ma20': round(ma20, 2),
                'vol_ratio': round(vol_ratio, 2), 'vol_ratio_5': round(vol_ratio_5, 2),
                'rise_today': round(rise_today, 2),
                'dist_ma5': round(dist_ma5, 2), 'dist_ma20': round(dist_ma20, 2),
                'rsi': round(rsi, 1),
                'grade': grade, 'signal_type': signal_type,
            })

    conn.close()
    grade_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
    signals.sort(key=lambda s: (grade_order.get(s['grade'], 9), -s['vol_ratio']))
    return signals


# ==================== 新闻正文抓取 ====================
def fetch_article_content(url, timeout=10):
    """抓取新闻页面正文内容（用于深度分析）"""
    if not url or not url.startswith('http'):
        return ''
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml',
        }
        resp = http_requests.get(url, headers=headers, timeout=timeout)
        resp.encoding = resp.apparent_encoding or 'utf-8'
        text = resp.text
        import re
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        sentences = text.split('。')
        content = []
        for s in sentences:
            s = s.strip()
            if len(s) > 30:
                content.append(s)
        result = '。'.join(content)[:5000]
        return result
    except Exception as e:
        return f'[抓取失败: {e}]'


# ==================== 深度新闻搜索（3维定向）====================
def search_single_stock_news_deep(code, name, signal_type='', grade=''):
    """深度搜索：3个维度 × 5条 + 抓取正文内容"""
    if grade == 'A级':
        focus = '业绩超预期 机构买入 研报推荐'
    elif grade == 'B级':
        focus = '业绩增长 订单饱满 行业景气'
    else:
        focus = '业绩公告 最新动态'

    queries = {
        '财报业绩': f'{name}({code}) {focus} 2026年一季报 业绩快报 净利润增长',
        '机构研报': f'{name}({code}) 券商研报 机构评级 目标价 外资持仓',
        '行业动态': f'{name}({code}) 行业政策 市场份额 竞争对手 产业链',
    }

    all_results = []
    all_raw = []

    for qtype, query in queries.items():
        try:
            parsed, raw = combined_search(query, count=5)
            if parsed:
                for item in parsed:
                    item['query_type'] = qtype
                    url = item.get('url', '')
                    if url:
                        seen_urls = [r.get('url', '') for r in all_results]
                        if url not in seen_urls:
                            content = fetch_article_content(url, timeout=8)
                            item['content'] = content if content else item.get('snippet', '')
                            all_results.append(item)
            all_raw.append(f'=== {qtype}搜索 ===\n{raw[:800]}')
        except Exception as e:
            all_raw.append(f'=== {qtype}搜索失败: {e} ===')
        time.sleep(0.5)

    raw_text = '\n\n'.join(all_raw)
    return {'news': all_results, 'raw_news': raw_text, 'fundamentals': []}


# ==================== 真实基本面数据 ====================
def _get_financial_data(code, name):
    """从搜索结果中提取财务数据"""
    query_revenue = f'{name}({code}) 2026年一季度 营收 净利润 同比增速'
    query_holders = f'{name}({code}) 机构持仓 股东人数 北向资金'
    try:
        rev_results, _ = combined_search(query_revenue, count=3)
        holder_results, _ = combined_search(query_holders, count=3)
    except:
        rev_results, holder_results = [], []

    data = {}
    import re
    for results in [rev_results, holder_results]:
        for r in results:
            snippet = r.get('snippet', '')
            m = re.search(r'营收[^0-9]*([0-9.]+)[亿万元]', snippet)
            if m and 'revenue' not in data: data['revenue'] = m.group(1)
            m = re.search(r'净利润[^0-9]*([0-9+\-.]+)[亿万元]', snippet)
            if m and 'net_profit' not in data: data['net_profit'] = m.group(1)
            m = re.search(r'同比[^0-9]*([0-9+\-.]+)%', snippet)
            if m and 'yoy_growth' not in data: data['yoy_growth'] = m.group(1)
            m = re.search(r'股东[^0-9]*([0-9]+)[户人家]', snippet)
            if m and 'shareholders' not in data: data['shareholders'] = m.group(1)
            if ('北向' in snippet or '外资' in snippet or '陆股通' in snippet) and 'foreign' not in data:
                data['foreign'] = '有信息'
    return data


# ==================== 搜索降级 ====================

def tavily_search(query, count=5):
    """Tavily搜索（备用，当MiniMax搜索次数用尽时）"""
    try:
        resp = http_requests.post('https://api.tavily.com/search', json={
            'api_key': TAVILY_API_KEY,
            'query': query,
            'max_results': count,
            'search_depth': 'basic',
        }, timeout=15)
        data = resp.json()
        results = []
        for r in data.get('results', []):
            results.append({
                'title': r.get('title', ''),
                'url': r.get('url', ''),
                'snippet': r.get('content', '')[:200],
                'date': r.get('published_date', ''),
            })
        return results
    except Exception as e:
        return [{'title': f'Tavily搜索失败: {e}', 'url': '', 'snippet': '', 'date': ''}]


_TENCENT_NEWS_CLI = r'C:\Users\12434\AppData\Local\tencent-news-cli\tencent-news-cli.exe'
_TENCENT_NEWS_APIKEY = '196ad90b-8c41-446b-bbd5-460fdef1858f'

def tencent_news_search(query, count=5):
    """腾讯新闻搜索（第三级备用）"""
    try:
        env = os.environ.copy()
        env['TENCENT_NEWS_APIKEY'] = _TENCENT_NEWS_APIKEY
        import subprocess
        result = subprocess.run(
            [_TENCENT_NEWS_CLI, 'search', query, '--limit', str(count)],
            capture_output=True, text=True, env=env, timeout=15,
            encoding='utf-8', errors='replace'
        )
        import re
        text = result.stdout
        # Skip header line and empty lines
        lines = [l.strip() for l in text.split('\n')]
        results = []
        item = {}
        for line in lines:
            # Skip header/blank lines
            if line.startswith('【腾讯新闻') or line.startswith('共 ') or not line:
                continue
            # Title line: "1. 标题：xxx" or "标题：xxx"
            m = re.match(r'^\d+\.\s*标题：(.+)', line)
            if m:
                if item:
                    results.append(item)
                item = {'title': m.group(1).strip(), 'url': '', 'snippet': '', 'date': ''}
            elif item:
                if line.startswith('链接:'):
                    item['url'] = line[3:].strip()
                elif line.startswith('发布时间:'):
                    item['date'] = line[5:].strip()
                elif line.startswith('摘要:'):
                    item['snippet'] = line[3:].strip()[:200]
                elif line.startswith('来源:'):
                    pass  # skip
        if item:
            results.append(item)
        return results
    except Exception as e:
        return []


def format_search_result(result):
    """格式化MiniMax搜索结果为文本"""
    if isinstance(result, str):
        return result
    return str(result)


def parse_search_result(text):
    """从搜索结果文本中提取结构化数据"""
    results = []
    if not text or text.startswith('Failed') or text.startswith('⚠️'):
        return results
    blocks = text.split('[')
    for block in blocks[1:]:
        lines = block.strip().split('\n')
        title = lines[0].split(']', 1)[-1].strip() if lines else ''
        url = ''
        snippet = ''
        date = ''
        for line in lines[1:]:
            line = line.strip()
            if line.startswith('链接:'):
                url = line[3:].strip()
            elif line.startswith('日期:'):
                date = line[3:].strip()
            elif line and not url:
                snippet = line[:200]
        if title:
            results.append({'title': title, 'url': url, 'snippet': snippet, 'date': date})
    return results


def combined_search(query, count=5):
    """优先MiniMax，失败降级到Tavily"""
    errors = []
    # 尝试MiniMax
    try:
        result = minimax_web_search(query, count=count)
        text = format_search_result(result)
        parsed = parse_search_result(text)
        if parsed and len(parsed) > 0:
            return parsed, text
        errors.append('MiniMax返回空结果(可能配额用完)')
    except Exception as e:
        errors.append(f'MiniMax搜索失败: {str(e)[:80]}')

    # 降级到Tavily
    try:
        results = tavily_search(query, count)
        # 忽略错误占位结果
        if results and not results[0].get('title', '').startswith('Tavily搜索失败'):
            text = '\n'.join([f'[{r["title"]}]\n链接:{r["url"]}\n{r["snippet"]}' for r in results])
            return results, text
        errors.append('Tavily返回空结果')
    except Exception as e:
        errors.append(f'Tavily搜索失败: {str(e)[:80]}')

    # 降级到腾讯新闻
    try:
        results = tencent_news_search(query, count)
        if results:
            text = '\n'.join([f'[{r["title"]}]\n链接:{r["url"]}\n摘要:{r["snippet"]}' for r in results])
            return results, text
        errors.append('腾讯新闻返回空结果')
    except Exception as e:
        errors.append(f'腾讯新闻搜索失败: {str(e)[:80]}')

    # 全部失败
    error_msg = '；'.join(errors)
    return [], f'⚠️ 搜索引擎暂不可用: {error_msg}'


def batch_search_news(stocks_batch):
    """批量搜索：每只股票3个维度新闻（摘要层，速度优先）"""
    results = {}
    for s in stocks_batch:
        code = s.get('code', '')
        name = s.get('name', '')
        grade = s.get('grade', '')
        signal_type = s.get('signal_type', '')
        try:
            news_data = search_single_stock_news_fast(code, name, grade)
            results[f'{code}_{name}'] = news_data
        except Exception as e:
            results[f'{code}_{name}'] = {'news': [], 'fundamentals': [], 'raw_news': f'搜索失败: {e}', 'raw_fund': ''}
        time.sleep(0.8)
    return {'_per_stock': results}


def search_single_stock_news_fast(code, name, grade=''):
    """快速搜索：3个维度 × 摘要（不抓正文，保证批量速度）"""
    if grade == 'A级':
        focus = '业绩超预期 机构买入 研报推荐'
    elif grade == 'B级':
        focus = '业绩增长 订单饱满 行业景气'
    else:
        focus = '业绩公告 最新动态'

    queries = {
        '财报业绩': f'{name}({code}) {focus} 2026年一季报 净利润增长',
        '机构研报': f'{name}({code}) 券商研报 机构评级 目标价 外资',
        '行业动态': f'{name}({code}) 行业政策 市场份额 产业链',
    }

    all_results = []
    all_raw = []

    for qtype, query in queries.items():
        try:
            parsed, raw = combined_search(query, count=4)
            if parsed:
                for item in parsed:
                    item['query_type'] = qtype
                    url = item.get('url', '')
                    if url:
                        seen = [r.get('url', '') for r in all_results]
                        if url not in seen:
                            all_results.append(item)
            all_raw.append(f'=== {qtype} ===\n{raw[:600]}')
        except Exception as e:
            all_raw.append(f'=== {qtype}失败: {e} ===')
        time.sleep(0.3)

    raw_text = '\n\n'.join(all_raw)
    return {'news': all_results, 'raw_news': raw_text, 'fundamentals': []}


def search_single_stock_news(code, name):
    """单只股票深度搜索（含正文抓取，用于重分析/单只深度分析）"""
    return search_single_stock_news_deep(code, name)


# ==================== MiniMax 搜索 + M2.7 分析 ====================

def minimax_search_stock_news(code: str, name: str) -> dict:
    """搜索股票新闻（优先MiniMax，降级Tavily）"""
    query = f'A股 {name}({code}) 最新消息 公告 利好利空'
    parsed, raw = combined_search(query, count=5)
    return {
        'news': parsed,
        'fundamentals': [],
        'raw_news': raw,
        'raw_fund': '',
    }


def m27_analyze(code: str, name: str, signal_info: dict, news_data: dict, fund: dict) -> dict:
    """用MiniMax M2.7模型进行深度综合分析"""

    # 从新闻数据中提取正文（深度分析核心）
    news_items = news_data.get('news', [])
    # 整理新闻内容：正文>摘要，保留前3条最相关的
    news_content_lines = []
    for i, item in enumerate(news_items[:3]):
        content = item.get('content', item.get('snippet', ''))
        qtype = item.get('query_type', '新闻')
        if content and len(content) > 20:
            news_content_lines.append(f"【{qtype}新闻{i+1}】{content[:800]}")
        elif item.get('snippet'):
            news_content_lines.append(f"【{qtype}新闻{i+1}摘要】{item['snippet'][:300]}")

    news_for_prompt = '\n\n'.join(news_content_lines) if news_content_lines else '无'

    # 从raw_news中补充（搜索摘要层）
    raw_snippets = []
    if news_data.get('raw_news'):
        raw = news_data['raw_news']
        import re
        # 提取所有摘要片段
        for m in re.findall(r'摘要[：:]([^\n]{50,300})', raw):
            raw_snippets.append(m.strip())
        for m in re.findall(r'\[([^\]]{10,})\]\n链接', raw):
            raw_snippets.append(m.strip()[:100])
    raw_for_prompt = '；'.join(raw_snippets[:8]) if raw_snippets else '无'

    prompt = f"""你是一个专业、资深的A股量化分析师，有10年以上二级市场投研经验。

## 股票基础信息
- 代码：{code}
- 名称：{name}
- 信号类型：{signal_info.get('signal_type', '未知')}
- MA评级：{signal_info.get('grade', '')}级
- 收盘价：{signal_info.get('close', '')}元
- 今日涨幅：{signal_info.get('rise_today', '')}%
- 量比：{signal_info.get('vol_ratio', '')}x（放量程度，越高说明资金关注度越大）
- 距MA5：{signal_info.get('dist_ma5', '')}%（>0说明在均线上方）
- 距MA20：{signal_info.get('dist_ma20', '')}%（>0说明中期趋势向上）
- RSI：{signal_info.get('rsi', '')}（>70超买，<30超卖）

## 价量统计基本面
{json.dumps(fund, ensure_ascii=False, indent=2)}

## 深度新闻内容（正文/摘要，共{len(news_items)}条）
{news_for_prompt}

## 搜索摘要补充
{raw_for_prompt}

## 你的分析任务（请严格按照以下结构输出）

### 一、信号质量验证（先判断这个MA信号是否可信）
- 今日放量是否真实？（量比解读）
- 是否有消息面支撑？（从新闻判断）
- 处于趋势哪个阶段？（初升/主升/尾声）

### 二、深度基本面分析
1. 最新财报关键数据（从新闻提取营收/利润/增速）
2. 与同业竞争对手相比的优劣势
3. 机构持仓动态（增仓/减仓/新进）
4. 估值合理性（PE历史分位、相对同业高估/低估）

### 三、资金面与情绪
1. 板块资金轮动角度（该板块当前是否主线）
2. 北向/外资动向（如果有）
3. 游资情绪（涨停股数、龙头股表现）

### 四、风险评估（至少3条）
1. 市场系统性风险
2. 个股负面因素（从新闻提取）
3. 技术面风险（是否超买/高位追高风险）

### 五、综合评分与操作计划
1. 综合评分（1-10分，10分最优，7分以上为优质机会）
2. 建仓计划（周一开盘价参考、是否值得追高、分批还是一次买）
3. 止损位（具体价格，MA5/成本-8%/固定比例均可）
4. 目标位（到哪里可以考虑减仓/止盈）

请严格用以下JSON格式返回（不要有任何其他内容）：
{{"score": 8, "signal_quality": "信号可信度评价", "fundamentals": "基本面摘要(50字内)", "risks": ["风险1", "风险2", "风险3"], "action_plan": "操作计划(80字内)", "conclusion": "一句话结论"}}
评分标准参考：8-10分=强烈推荐（基本面扎实+信号强劲+板块主线）；6-7分=推荐（信号有效但需注意风险）；4-5分=观望；<4分=回避"""

    ai_text = _call_m27_raw(prompt)
    return _parse_m27_result_deep(ai_text, news_data)


def _call_m27_raw(prompt: str) -> str:
    """调用M2.7 API，返回原始文本"""
    resp = http_requests.post(MINIMAX_API_URL,
        headers={
            'x-api-key': MINIMAX_API_KEY,
            'anthropic-version': '2023-06-01',
            'content-type': 'application/json'
        },
        json={
            'model': 'MiniMax-M2.7',
            'max_tokens': 8000,
            'messages': [{'role': 'user', 'content': prompt}]
        }, timeout=180)
    data = resp.json()
    for block in data.get('content', []):
        if isinstance(block, dict) and block.get('type') == 'text':
            return block['text']
    return ''


def _call_m27_api(prompt: str) -> dict:
    """调用M2.7 API并解析JSON结果（用于综合分析等简单场景）"""
    ai_text = _call_m27_raw(prompt)
    return _parse_m27_result(ai_text, None)


def _parse_m27_result(ai_text: str, news_data: dict = None) -> dict:
    """解析M2.7返回的JSON结果"""
    import re
    score = 5
    positives = []
    issues = []
    suggestion = ''
    conclusion = ''

    json_match = re.search(r'\{[^{}]*"score"[^{}]*\}', ai_text)
    if not json_match:
        json_match = re.search(r'\{.*?"score":\s*\d+\.?\d*', ai_text, re.DOTALL)
    if json_match:
        raw = json_match.group()
        try:
            result = json.loads(raw)
        except:
            result = {'score': 5}
            score_m = re.search(r'"score":\s*(\d+\.?\d*)', raw)
            if score_m:
                result['score'] = float(score_m.group(1))
            pos_m = re.search(r'"positives":\s*\[(.*?)\]', raw, re.DOTALL)
            if pos_m:
                result['positives'] = [s.strip().strip('"') for s in pos_m.group(1).split(',') if s.strip().strip('"')]
            iss_m = re.search(r'"issues":\s*\[(.*?)\]', raw, re.DOTALL)
            if iss_m:
                result['issues'] = [s.strip().strip('"') for s in iss_m.group(1).split(',') if s.strip().strip('"')]
            sug_m = re.search(r'"suggestion":\s*"(.*?)"', raw, re.DOTALL)
            if sug_m:
                result['suggestion'] = sug_m.group(1)
            con_m = re.search(r'"conclusion":\s*"(.*?)"', raw, re.DOTALL)
            if con_m:
                result['conclusion'] = con_m.group(1)

        score = max(1, min(10, int(round(float(result.get('score', 5))))))
        positives = result.get('positives', [])
        issues = result.get('issues', [])
        suggestion = result.get('suggestion', '')
        conclusion = result.get('conclusion', '')
    else:
        score_match = re.search(r'(?:评分|score)[：:]\s*(\d+)', ai_text, re.I)
        if score_match:
            score = max(1, min(10, int(score_match.group(1))))
        suggestion = ai_text[:200]

    report_parts = []
    if positives:
        report_parts.append(f"【利好因素】{'；'.join(positives[:5])}")
    if issues:
        report_parts.append(f"【风险因素】{'；'.join(issues[:5])}")
    if suggestion:
        report_parts.append(f"【操作建议】{suggestion}")
    if conclusion:
        report_parts.append(f"【结论】{conclusion}")
    report_parts.append("⚠️ 以上分析仅供参考，不构成投资建议。")

    news_links = []
    if news_data:
        for item in (news_data.get('news') or []) + (news_data.get('fundamentals') or []):
            if item.get('title') and item.get('url'):
                news_links.append({'title': item['title'], 'url': item['url']})
        news_links = list({l['url']: l for l in news_links}.values())[:8]

    return {
        'score': score,
        'analysis': '\n'.join(report_parts),
        'news_links': news_links,
        'news_summary': '；'.join([n.get('title', '')[:40] for n in (news_data or {}).get('news', [])[:3]]) or '无重大新闻',
        'ai_raw': ai_text[:500],
    }


def _parse_m27_result_deep(ai_text: str, news_data: dict = None) -> dict:
    """解析深度分析JSON结果（新格式）"""
    import re
    score = 5
    signal_quality = ''
    fundamentals = ''
    risks = []
    action_plan = ''
    conclusion = ''

    # 尝试提取JSON
    json_match = re.search(r'\{.*\}', ai_text, re.DOTALL)
    if json_match:
        try:
            result = json.loads(json_match.group())
            score = max(1, min(10, int(round(float(result.get("score", 5))))))
            signal_quality = result.get('signal_quality', '')
            fundamentals = result.get('fundamentals', '')
            risks = result.get('risks', [])
            action_plan = result.get('action_plan', '')
            conclusion = result.get('conclusion', '')
        except:
            pass

    # 构建人类可读的分析报告
    report_parts = []
    if signal_quality:
        report_parts.append(f"【信号验证】{signal_quality}")
    if fundamentals:
        report_parts.append(f"【基本面】{fundamentals}")
    if risks:
        report_parts.append(f"【风险】{'；'.join(risks[:5])}")
    if action_plan:
        report_parts.append(f"【操作计划】{action_plan}")
    if conclusion:
        report_parts.append(f"【结论】{conclusion}")
    report_parts.append("⚠️ AI分析仅供参考，不构成投资建议。")

    news_links = []
    if news_data:
        for item in (news_data.get('news') or []):
            if item.get('url') and item.get('title'):
                news_links.append({'title': item['title'], 'url': item['url']})
        news_links = news_links[:8]

    return {
        'score': score,
        'analysis': '\n'.join(report_parts),
        'news_links': news_links,
        'news_summary': '；'.join([n.get('title', '')[:40] for n in (news_data or {}).get('news', [])[:3]]) or '无重大新闻',
        'ai_raw': ai_text[:500],
    }

    try:
        resp = http_requests.post(MINIMAX_API_URL,
            headers={
                'x-api-key': MINIMAX_API_KEY,
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json'
            },
            json={
                'model': 'MiniMax-M2.7',
                'max_tokens': 2000,
                'messages': [{'role': 'user', 'content': prompt}]
            }, timeout=30)

        data = resp.json()
        # 提取text内容
        ai_text = ''
        for block in data.get('content', []):
            if isinstance(block, dict) and block.get('type') == 'text':
                ai_text = block['text']
                break

        # 尝试解析JSON
        import re
        # 先找完整JSON
        json_match = re.search(r'\{[^{}]*"score"[^{}]*\}', ai_text)
        if not json_match:
            # 尝试修复截断的JSON
            json_match = re.search(r'\{.*?"score":\s*\d+\.?\d*', ai_text, re.DOTALL)
        if json_match:
            raw = json_match.group()
            # 尝试直接解析
            try:
                result = json.loads(raw)
            except:
                # 修复截断JSON：提取关键字段
                score_m = re.search(r'"score":\s*(\d+\.?\d*)', raw)
                result = {'score': float(score_m.group(1)) if score_m else 5}
                # 提取positives
                pos_m = re.search(r'"positives":\s*\[(.*?)\]', raw, re.DOTALL)
                if pos_m:
                    result['positives'] = [s.strip().strip('"') for s in pos_m.group(1).split(',') if s.strip().strip('"')]
                # 提取issues
                iss_m = re.search(r'"issues":\s*\[(.*?)\]', raw, re.DOTALL)
                if iss_m:
                    result['issues'] = [s.strip().strip('"') for s in iss_m.group(1).split(',') if s.strip().strip('"')]
                # 提取suggestion
                sug_m = re.search(r'"suggestion":\s*"(.*?)"', raw, re.DOTALL)
                if sug_m:
                    result['suggestion'] = sug_m.group(1)
                # 提取conclusion
                con_m = re.search(r'"conclusion":\s*"(.*?)"', raw, re.DOTALL)
                if con_m:
                    result['conclusion'] = con_m.group(1)

            score = max(1, min(10, int(round(float(result.get('score', 5))))))
            positives = result.get('positives', [])
            issues = result.get('issues', [])
            suggestion = result.get('suggestion', '')
            conclusion = result.get('conclusion', '')
        else:
            # JSON解析失败，用文本分析
            score = 5
            positives = []
            issues = []
            suggestion = ai_text[:200]
            conclusion = ''

            # 尝试从文本中提取分数
            score_match = re.search(r'(?:评分|score)[：:]\s*(\d+)', ai_text, re.I)
            if score_match:
                score = max(1, min(10, int(score_match.group(1))))

        # 构建分析报告
        report_parts = []
        if positives:
            report_parts.append(f"【利好因素】{'；'.join(positives[:5])}")
        if issues:
            report_parts.append(f"【风险因素】{'；'.join(issues[:5])}")
        if suggestion:
            report_parts.append(f"【操作建议】{suggestion}")
        if conclusion:
            report_parts.append(f"【结论】{conclusion}")
        report_parts.append("⚠️ 以上分析仅供参考，不构成投资建议。")

        # 收集新闻链接
        news_links = []
        for item in (news_data.get('news') or []) + (news_data.get('fundamentals') or []):
            if item.get('title') and item.get('url'):
                news_links.append({'title': item['title'], 'url': item['url']})
        news_links = list({l['url']: l for l in news_links}.values())[:8]

        news_summary = '；'.join([n.get('title', '')[:40] for n in news_data.get('news', [])[:3]]) or '无重大新闻'

        return {
            'score': score,
            'analysis': '\n'.join(report_parts),
            'news_links': news_links,
            'news_summary': news_summary,
            'ai_raw': ai_text[:500],
        }

    except Exception as e:
        # M2.7调用失败，降级为基础评分
        return _fallback_score(code, name, signal_info, news_data, fund, str(e))


def _fallback_score(code, name, signal_info, news_data, fund, error=''):
    """降级评分（M2.7不可用时）"""
    score = 5
    positives = []
    issues = []

    vr = signal_info.get('vol_ratio', 0) or 0
    if vr >= 3:
        score += 1; positives.append('放量明显')
    elif vr >= 2:
        score += 0.5; positives.append('量能充足')
    elif vr < 1.3:
        score -= 1; issues.append('量能不足')

    rise = signal_info.get('rise_today', 0) or 0
    if rise > 5:
        score -= 1; issues.append('涨幅过大')
    elif 0 < rise <= 2:
        score += 0.5; positives.append('涨幅温和')

    grade = signal_info.get('grade', '')
    if grade == 'A':
        score += 1; positives.append('A级信号')
    elif grade == 'D':
        score -= 1

    score = max(1, min(10, round(score)))

    parts = []
    if positives:
        parts.append(f"【利好因素】{'；'.join(positives)}")
    if issues:
        parts.append(f"【风险因素】{'；'.join(issues)}")
    if error:
        parts.append(f"【注意】AI分析暂不可用({error[:50]})，已降级为基础评分")
    parts.append("⚠️ 以上分析仅供参考，不构成投资建议。")

    news_links = []
    for item in (news_data.get('news') or []):
        if item.get('title') and item.get('url'):
            news_links.append({'title': item['title'], 'url': item['url']})

    return {
        'score': score,
        'analysis': '\n'.join(parts),
        'news_links': news_links[:8],
        'news_summary': '；'.join([n.get('title', '')[:40] for n in news_data.get('news', [])[:3]]) or '无重大新闻',
    }


def _fetch_indicators(code: str, signal_date: str) -> dict:
    """从数据库计算技术指标（MA5/MA10/MA20/Vol_ratio/RSI）"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT date, open, high, low, close, volume
        FROM stock_daily
        WHERE code=? AND date<=? AND close IS NOT NULL
        ORDER BY date DESC LIMIT 60
    """, (code, signal_date))
    rows = c.fetchall()
    conn.close()
    if len(rows) < 20:
        return {}
    prices = [float(r[4]) for r in rows]
    volumes = [float(r[5]) for r in rows]
    close = prices[0]
    ma5 = np.mean(prices[:5])
    ma10 = np.mean(prices[:10])
    ma20 = np.mean(prices[:20])
    vol5 = np.mean(volumes[:5])
    vol_ratio = close / vol5 if vol5 > 0 else 0
    rsi_val = _rsi(prices, 14) if len(prices) >= 15 else 50
    dist_ma5 = (close - ma5) / ma5 * 100 if ma5 > 0 else 0
    dist_ma20 = (close - ma20) / ma20 * 100 if ma20 > 0 else 0
    return {
        'ma5': round(ma5, 2),
        'ma10': round(ma10, 2),
        'ma20': round(ma20, 2),
        'vol_ratio': round(vol_ratio, 2),
        'rsi': round(rsi_val, 1),
        'dist_ma5': round(dist_ma5, 2),
        'dist_ma20': round(dist_ma20, 2),
    }

def _get_fundamentals(code, name, signal_date):
    """获取基本面数据：历史价量统计 + 搜索财务数据"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT date,close,volume FROM stock_daily WHERE code=? AND date<=? AND close IS NOT NULL ORDER BY date DESC LIMIT 30", (code, signal_date))
    rows = c.fetchall()
    conn.close()
    if len(rows) < 5:
        return {'error': '数据不足'}
    prices = [r[1] for r in rows]
    volumes = [r[2] for r in rows]
    current = prices[0]
    high_20 = max(prices[:min(20, len(prices))])
    low_20 = min(prices[:min(20, len(prices))])
    avg_vol = np.mean(volumes[:10])

    # 尝试获取财务数据
    try:
        fin_data = _get_financial_data(code, name)
    except:
        fin_data = {}

    result = {
        'current_price': round(current, 2),
        'high_20d': round(high_20, 2),
        'low_20d': round(low_20, 2),
        'price_range': f"{round(low_20, 2)}-{round(high_20, 2)}",
        'avg_volume_10d': round(avg_vol, 0),
        'price_vs_high': round((current - high_20) / high_20 * 100, 2),
        'price_vs_low': round((current - low_20) / low_20 * 100, 2),
        'data_points': len(rows),
    }
    result.update(fin_data)
    return result


def _create_analysis_run(signal_date, stock_count):
    conn = sqlite3.connect(ANALYSIS_DB)
    c = conn.cursor()
    # Generate run_code: yyyymmdd-NNN
    ymd = signal_date.replace('-', '')
    c.execute("SELECT COUNT(*) FROM analysis_runs WHERE signal_date=?", (signal_date,))
    seq = c.fetchone()[0] + 1
    run_code = f"{ymd}-{seq:03d}"
    c.execute("INSERT INTO analysis_runs (run_date,signal_date,created_at,stock_count,run_code) VALUES(?,?,?,?,?)",
              (datetime.now().strftime('%Y-%m-%d'), signal_date, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), stock_count, run_code))
    run_id = c.lastrowid
    conn.commit()
    conn.close()
    return run_id


def _save_result(run_id, seq, code, name, signal_type, grade, score, analysis, news_links, fund):
    conn = sqlite3.connect(ANALYSIS_DB)
    c = conn.cursor()
    c.execute("INSERT INTO analysis_results (run_id,seq,code,name,signal_type,grade,score,analysis,news_links,fundamentals,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (run_id, seq, code, name, signal_type, grade, score, analysis,
               json.dumps(news_links, ensure_ascii=False),
               json.dumps(fund, ensure_ascii=False),
               datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    result_id = c.lastrowid
    conn.close()
    return result_id


# ==================== API路由 ====================

@app.route('/')
def index():
    resp = send_file('index.html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@app.route('/api/dates')
def api_dates():
    return jsonify({'dates': get_available_dates()})

@app.route('/api/signals')
def api_signals():
    date = request.args.get('date', '')
    if not date:
        return jsonify({'error': '请提供日期参数'}), 400
    signals = calc_signals_for_date(date)
    return jsonify({
        'date': date, 'count': len(signals), 'signals': signals,
        'summary': {
            'A': len([s for s in signals if s['grade'] == 'A']),
            'B': len([s for s in signals if s['grade'] == 'B']),
            'C': len([s for s in signals if s['grade'] == 'C']),
            'D': len([s for s in signals if s['grade'] == 'D']),
        }
    })


def _batch_search_news(stocks_batch):
    """批量搜索（兼容旧调用，委托新函数）"""
    return batch_search_news(stocks_batch)


@app.route('/api/full_analyze', methods=['POST'])
def api_full_analyze():
    """SSE流式分析：批量搜索（5只共享1次搜索）→ M2.7分析 → 逐个返回结果，支持中断"""
    data = request.json
    stocks = data.get('stocks', [])
    signal_date = data.get('signal_date', '')

    if not stocks:
        return jsonify({'error': '请选择要分析的股票'}), 400

    run_id = _create_analysis_run(signal_date, len(stocks))
    _analysis_aborted[run_id] = False

    def generate():
        total = len(stocks)
        batch_size = 5
        analyzed = 0

        for batch_start in range(0, total, batch_size):
            # 检查中断
            if _analysis_aborted.get(run_id):
                yield f"data: {json.dumps({'type': 'aborted', 'message': f'用户中断，已分析 {analyzed}/{total} 只', 'run_id': run_id}, ensure_ascii=False)}\n\n"
                break

            batch = stocks[batch_start:batch_start + batch_size]

            # 批量搜索
            names = ' '.join([f'{s.get("name","")}' for s in batch])
            yield f"data: {json.dumps({'type': 'progress', 'stage': 'search', 'code': '', 'name': names, 'current': batch_start + 1, 'total': total}, ensure_ascii=False)}\n\n"

            try:
                shared_news = batch_search_news(batch)
            except Exception as e:
                shared_news = {'news': [], 'fundamentals': [], 'raw_news': f'搜索失败: {e}', 'raw_fund': ''}

            # 逐只分析
            for i, stock in enumerate(batch):
                if _analysis_aborted.get(run_id):
                    yield f"data: {json.dumps({'type': 'aborted', 'message': f'用户中断，已分析 {analyzed}/{total} 只', 'run_id': run_id}, ensure_ascii=False)}\n\n"
                    return

                code = stock.get('code', '')
                name = stock.get('name', '')
                yield f"data: {json.dumps({'type': 'progress', 'stage': 'analyze', 'code': code, 'name': name, 'current': batch_start + i + 1, 'total': total}, ensure_ascii=False)}\n\n"

                fund = _get_fundamentals(code, name, signal_date)
                indicators = _fetch_indicators(code, signal_date)
                stock = {**stock, **indicators}
                # 有已有新闻则复用，不重新搜索
                existing_news = stock.get('news_links', [])
                if existing_news and len(existing_news) > 0:
                    stock_news = {'news': existing_news[:5], 'raw_news': '复用历史新闻'}
                else:
                    stock_news = shared_news.get('_per_stock', {}).get(f'{code}_{name}', shared_news)
                result = m27_analyze(code, name, stock, stock_news, fund)
                result['code'] = code
                result['name'] = name
                result['signal_type'] = stock.get('signal_type', '')
                result['grade'] = stock.get('grade', '')
                result['fundamentals'] = fund
                result['run_id'] = run_id

                _save_result(run_id, analyzed + 1, code, name, stock.get('signal_type', ''), stock.get('grade', ''),
                            result['score'], result['analysis'], result.get('news_links', []), fund)
                analyzed += 1

                yield f"data: {json.dumps({'type': 'result', 'data': result}, ensure_ascii=False)}\n\n"

            if batch_start + batch_size < total:
                yield f"data: {json.dumps({'type': 'batch_pause', 'message': f'已完成{min(batch_start + batch_size, total)}/{total}，等待2秒...'}, ensure_ascii=False)}\n\n"
                time.sleep(2)

        yield f"data: {json.dumps({'type': 'done', 'run_id': run_id, 'total': total}, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/abort', methods=['POST'])
def api_abort():
    """中断分析"""
    data = request.json
    run_id = data.get('run_id')
    if run_id:
        _analysis_aborted[run_id] = True
        return jsonify({'aborted': True})
    return jsonify({'error': '需要run_id'}), 400


@app.route('/api/reanalyze', methods=['POST'])
def api_reanalyze():
    """重新分析单只股票（可复用已有新闻，或重新搜索）"""
    data = request.json
    code = data.get('code', '')
    name = data.get('name', '')
    signal = data.get('signal', {})
    signal_date = data.get('signal_date', '')
    reuse_news = data.get('reuse_news')
    search_news = data.get('search_news', False)

    if not code:
        return jsonify({'error': '需要股票代码'}), 400

    if reuse_news and not search_news:
        news_data = reuse_news
    else:
        try:
            news_data = search_single_stock_news(code, name)
        except Exception as e:
            news_data = {'news': [], 'raw_news': f'搜索失败: {e}'}

    fund = _get_fundamentals(code, name, signal_date)
    indicators = _fetch_indicators(code, signal_date)
    signal_info = {**signal, **indicators}
    try:
        result = m27_analyze(code, name, signal_info, news_data, fund)
    except Exception as e:
        result = _fallback_score(code, name, signal_info, news_data, fund, str(e))
    result['code'] = code
    result['name'] = name
    result['signal_type'] = signal.get('signal_type', '')
    result['grade'] = signal.get('grade', '')
    result['fundamentals'] = fund
    result['news_links'] = [{'title': n.get('title', ''), 'url': n.get('url', '')} for n in (news_data.get('news') or []) if n.get('title')]
    result['news_summary'] = '；'.join([n.get('title', '')[:40] for n in (news_data.get('news') or [])[:3]]) or '无重大新闻'
    if news_data.get('raw_news', '').startswith('⚠️'):
        result['search_error'] = news_data['raw_news']

    return jsonify(result)


@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    """单股分析（兼容前端逐只分析模式）"""
    data = request.json
    code = data.get('code', '')
    name = data.get('name', '')
    grade = data.get('grade', '')
    signal_type = data.get('signal_type', '')
    signal_date = data.get('signal_date', '')

    if not code:
        return jsonify({'error': '需要股票代码'}), 400

    signal_info = {'code': code, 'name': name, 'grade': grade, 'signal_type': signal_type}

    # 获取技术指标（量比/RSI/MA距离等）
    if signal_date:
        indicators = _fetch_indicators(code, signal_date)
        signal_info = {**signal_info, **indicators}

    # 检查是否有已有新闻要复用
    existing_news = data.get('news_links', [])
    if existing_news and isinstance(existing_news, list):
        # 复用已有新闻，不重新搜索
        news_data = {'news': [], 'raw_news': '复用历史新闻'}
        if len(existing_news) > 0:
            news_data['news'] = [{'title': n.get('title', '') or n.get('title', '新闻'), 'url': n.get('url', '')} for n in existing_news[:5]]
    else:
        try:
            news_data = search_single_stock_news(code, name)
        except Exception as e:
            news_data = {'news': [], 'raw_news': f'搜索失败: {e}'}

    fund = _get_fundamentals(code, name, signal_date)
    try:
        result = m27_analyze(code, name, signal_info, news_data, fund)
    except Exception as e:
        result = _fallback_score(code, name, signal_info, news_data, fund, str(e))

    result['code'] = code
    result['name'] = name
    result['signal_type'] = signal_type
    result['grade'] = grade
    result['fundamentals'] = fund
    result['news_links'] = [{'title': n.get('title', ''), 'url': n.get('url', '')} for n in (news_data.get('news') or []) if n.get('title')]
    return jsonify(result)


@app.route('/api/comprehensive', methods=['POST'])
def api_comprehensive():
    """综合分析：基于已有的逐只分析结果（含新闻链接），重新综合评估，生成详细报告+排名"""
    data = request.json
    results = data.get('results', [])
    signal_date = data.get('signal_date', '') or '当日'

    if not results:
        return jsonify({'error': '没有分析结果'}), 400

    # 构建完整的个股详细信息（包含新闻链接）
    stock_details = []
    for i, r in enumerate(results):
        # 收集新闻标题
        news_titles = []
        for nl in (r.get('news_links') or []):
            title = nl.get('title', '')
            if title:
                news_titles.append(title)
        news_str = '；'.join(news_titles) if news_titles else '无重大新闻'
        # 原始分析摘要（取前200字，避免prompt过长）
        analysis_raw = (r.get('analysis') or '').replace('\n', ' ').replace('⚠️', '').strip()[:200]
        stock_details.append({
            'idx': i + 1,
            'code': r.get('code', ''),
            'name': r.get('name', ''),
            'grade': r.get('grade', ''),
            'score': r.get('score') or 0,
            'signal': r.get('signal_type', ''),
            'news': news_str,
            'analysis_raw': analysis_raw
        })

    # 按评分排序，分高→低（处理None score）
    stock_details.sort(key=lambda x: x['score'] if x['score'] is not None else 0, reverse=True)

    # 构建详细stock list用于prompt
    stock_lines = []
    for s in stock_details:
        stock_lines.append(
            f"【{s['idx']}】{s['name']}({s['code']}) | 原始评分:{s['score']}分 | "
            f"评级:{s['grade']}级 | 信号:{s['signal']} | "
            f"新闻:{s['news']} | 分析:{s['analysis_raw']}"
        )

    prompt = f"""你是一个专业的A股量化分析师。现有{signal_date} MA策略扫描的{len(results)}只股票完整数据，请进行综合分析。

## 完整个股数据（按原始评分排序）
{chr(10).join(stock_lines)}

## 你的任务

### 第一步：综合评分（1-10分）
结合以下维度，对每只股票重新给出综合评分（可参考原始评分，但必须结合新闻和原始分析做调整）：
- 新闻利好/利空程度（新闻多的重点看）
- 原始分析中的关键利好/风险因素
- 量价信号质量（量比、RSI、距MA5/20距离）
- 板块与大盘环境

### 第二步：生成详细综合报告
报告必须包含以下章节，500字以上：
1. **整体市场判断**：今日信号整体质量、强势板块、弱势板块、市场情绪
2. **重点关注股票TOP5**：每只说明理由（结合新闻+分析+评分调整原因）
3. **风险警示**：整体风险 + 高风险个股名单（评分下调的股票）
4. **仓位与操作建议**：总仓位建议、分批建仓计划、止损设置
5. **板块机会**：从信号中发现的板块轮动机会

### 第三步：输出最终排名
在报告最后，按综合评分从高到低列出前10名股票，格式：
排名|代码|名称|综合评分|调分原因（1句话）

请用JSON格式返回，包含3个字段：
{{"report": "详细综合报告（中文，500字以上）", "ranking": [{{"rank":1,"code":"xxx","name":"xxx","score":8.5,"reason":"..."}}, ...], "market_summary": "一句话总结"}}

只返回JSON，不要有其他内容。"""

    try:
        # 直接调用raw，解析M2.7返回的JSON（包含report和ranking）
        ai_text = _call_m27_raw(prompt)
        print(f"[DEBUG] ai_text length: {len(ai_text)}, first 200: {ai_text[:200]}")

        import re
        print(f"[DEBUG] ai_text length: {len(ai_text)}, first 300: {ai_text[:300]}")
        # 尝试提取JSON - 用平衡括号计数找真正的JSON边界
        def extract_json(text):
            """在文本中找最外层平衡的JSON对象"""
            start = text.find('{')
            if start == -1:
                return None, None
            # 从找到的{开始，逐字符找匹配的}
            brace_count = 0
            i = start
            while i < len(text):
                c = text[i]
                if c == '{':
                    brace_count += 1
                elif c == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        return text[start:i+1], start
                i += 1
            return None, start

        json_str, json_start = extract_json(ai_text)
        report_text = ''
        ranking_list = []
        market_summary = ''

        if json_str:
            try:
                parsed = json.loads(json_str)
                report_text = parsed.get('report', '')
                ranking_list = parsed.get('ranking', [])
                market_summary = parsed.get('market_summary', '')
                print(f"[DEBUG] JSON parsed OK, report len={len(report_text)}, ranking count={len(ranking_list)}")
            except Exception as e:
                print(f"[DEBUG] JSON parse failed: {e}, trying partial...")
                # 降级：尝试直接读ai_text内容
                try:
                    parsed = json.loads(ai_text.strip())
                    report_text = parsed.get('report', ai_text[:500])
                    ranking_list = parsed.get('ranking', [])
                    market_summary = parsed.get('market_summary', '')
                except:
                    pass
        if not report_text:
            # 降级：返回原始简单分析
            report_text = ai_text[:2000] if ai_text else '综合分析生成失败，请重试。'
            ranked = sorted(results, key=lambda x: x.get('score', 0), reverse=True)
            ranking_list = [{'rank': i+1, 'code': r.get('code',''), 'name': r.get('name',''), 'score': r.get('score',0), 'reason': '基于原始评分排序'} for i, r in enumerate(ranked[:10])]
            market_summary = f'共分析{len(results)}只股票，整体信号偏积极'

        # 把原始数据中的score作为参考分传回去，前端用它显示原始评分列
        for r in results:
            r['_orig_score'] = r.get('score', 0)

        return jsonify({
            'comprehensive': report_text,
            'market_summary': market_summary,
            'ranking': ranking_list,
            'stock_count': len(results),
            'signal_date': signal_date,
        })
    except Exception as e:
        ranked = sorted(results, key=lambda x: x.get('score', 0), reverse=True)
        summary = f"【整体判断】共分析{len(results)}只股票，平均评分{sum(r.get('score',0) for r in results)/len(results):.1f}分。\n"
        summary += f"【TOP3推荐】" + '、'.join([f"{r.get('name','')}({r.get('score',0)}分)" for r in ranked[:3]]) + "\n"
        summary += f"【风险提示】" + '、'.join([f"{r.get('name','')}" for r in ranked[-3:] if r.get('score',0)<5]) + "需注意风险。\n"
        summary += f"【操作建议】轻仓试探，严格止损-13%。"
        return jsonify({
            'comprehensive': summary,
            'market_summary': f'共{len(results)}只，整体信号中性',
            'ranking': [{'rank':i+1,'code':r.get('code',''),'name':r.get('name',''),'score':r.get('score',0),'reason':'基于原始评分'} for i,r in enumerate(ranked[:10])],
            'stock_count': len(results),
            'signal_date': signal_date,
        })


@app.route('/api/save_ranking', methods=['POST'])
def api_save_ranking():
    data = request.json
    run_id = data.get('run_id')
    summary = data.get('summary', '')
    conn = sqlite3.connect(ANALYSIS_DB)
    c = conn.cursor()
    c.execute("UPDATE analysis_runs SET summary=? WHERE id=?", (summary, run_id))
    conn.commit()
    conn.close()
    return jsonify({'saved': True})


@app.route('/api/save_comprehensive', methods=['POST'])
def api_save_comprehensive():
    """保存综合分析报告到历史记录，同时更新各股票的综合评分"""
    data = request.json
    run_id = data.get('run_id')
    comprehensive_report = data.get('comprehensive_report', '')
    comprehensive_summary = data.get('comprehensive_summary', '')
    ranking = data.get('ranking', [])  # [{code, name, score, reason, grade}]

    if not run_id:
        return jsonify({'error': '缺少run_id'}), 400

    conn = sqlite3.connect(ANALYSIS_DB)
    c = conn.cursor()
    # 保存综合报告到runs表
    c.execute("UPDATE analysis_runs SET comprehensive_report=?, comprehensive_summary=? WHERE id=?",
              (comprehensive_report, comprehensive_summary, run_id))

    # 更新各股票的综合评分到results表
    for item in ranking:
        c.execute("""
            UPDATE analysis_results
            SET comprehensive_score=?, comprehensive_grade=?, comprehensive_reason=?
            WHERE run_id=? AND code=?
        """, (item.get('score'), item.get('grade', ''), item.get('reason', ''), run_id, item.get('code', '')))

    conn.commit()
    conn.close()
    return jsonify({'saved': True, 'run_id': run_id})


@app.route('/api/update_result', methods=['POST'])
def api_update_result():
    """更新单条分析结果（score/analysis/news_links）"""
    data = request.json
    run_id = data.get('run_id')
    code = data.get('code', '')
    score = data.get('score')
    analysis = data.get('analysis', '')
    news_links = data.get('news_links', [])
    grade = data.get('grade', '')

    if not run_id or not code:
        return jsonify({'error': '需要run_id和code'}), 400

    conn = sqlite3.connect(ANALYSIS_DB)
    c = conn.cursor()
    c.execute("""
        UPDATE analysis_results
        SET score=?, analysis=?, news_links=?, grade=?
        WHERE run_id=? AND code=?
    """, (score, analysis, json.dumps(news_links, ensure_ascii=False), grade, run_id, code))
    conn.commit()
    affected = c.rowcount
    conn.close()
    return jsonify({'updated': affected > 0})


@app.route('/api/comprehensive/<int:run_id>', methods=['GET'])
def api_get_comprehensive(run_id):
    """获取某次历史记录的综合分析报告"""
    conn = sqlite3.connect(ANALYSIS_DB)
    c = conn.cursor()
    c.execute("SELECT comprehensive_report, comprehensive_summary, signal_date, stock_count FROM analysis_runs WHERE id=?", (run_id,))
    row = c.fetchone()
    conn.close()
    if not row or not row[0]:
        return jsonify({'error': '未找到综合分析报告'}), 404
    return jsonify({
        'comprehensive_report': row[0],
        'comprehensive_summary': row[1] or '',
        'signal_date': row[2],
        'stock_count': row[3]
    })


@app.route('/api/history')
def api_history():
    limit = request.args.get('limit', 20, type=int)
    conn = sqlite3.connect(ANALYSIS_DB)
    c = conn.cursor()
    c.execute("SELECT id,run_date,signal_date,created_at,stock_count,summary,comprehensive_summary,run_code FROM analysis_runs ORDER BY created_at DESC LIMIT ?", (limit,))
    runs = [{'id': r[0], 'run_date': r[1], 'signal_date': r[2], 'created_at': r[3], 'stock_count': r[4], 'summary': r[5], 'comprehensive_summary': r[6] or '', 'run_code': r[7] or ''} for r in c.fetchall()]
    conn.close()
    return jsonify({'history': runs})


@app.route('/api/history/<int:run_id>')
def api_history_detail(run_id):
    conn = sqlite3.connect(ANALYSIS_DB)
    c = conn.cursor()
    c.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,))
    run = c.fetchone()
    c.execute("SELECT seq,code,name,signal_type,grade,score,analysis,news_links,fundamentals,created_at,comprehensive_grade,comprehensive_score,comprehensive_reason FROM analysis_results WHERE run_id=? ORDER BY seq ASC", (run_id,))
    results = [{'seq': r[0], 'code': r[1], 'name': r[2], 'signal_type': r[3], 'grade': r[4], 'score': r[5], 'analysis': r[6], 'news_links': json.loads(r[7]) if r[7] else [], 'fundamentals': json.loads(r[8]) if r[8] else {}, 'created_at': r[9], 'comprehensive_grade': r[10] or '', 'comprehensive_score': r[11] if r[11] is not None else None, 'comprehensive_reason': r[12] or ''} for r in c.fetchall()]
    conn.close()
    if not run:
        return jsonify({'error': '未找到'}), 404
    return jsonify({'run_id': run_id, 'run_date': run[1], 'signal_date': run[2], 'created_at': run[3], 'stock_count': run[4], 'summary': run[5], 'comprehensive_report': run[6] or '', 'comprehensive_summary': run[7] or '', 'results': results})


@app.route('/api/stock_kline')
def api_stock_kline():
    code = request.args.get('code', '')
    days = request.args.get('days', 60, type=int)
    if not code:
        return jsonify({'error': '请提供股票代码'}), 400
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT date,open,high,low,close,volume FROM stock_daily WHERE code=? AND close IS NOT NULL ORDER BY date DESC LIMIT ?", (code, days))
    rows = c.fetchall()
    conn.close()
    kline = [{'date': r[0], 'open': r[1], 'high': r[2], 'low': r[3], 'close': r[4], 'volume': r[5]} for r in reversed(rows)]
    return jsonify({'code': code, 'kline': kline})


if __name__ == '__main__':
    import subprocess, os, sys, time

    # 先杀掉所有旧的app.py进程（排除自己）
    my_pid = os.getpid()
    try:
        result = subprocess.run(
            ['wmic', 'process', 'where', "name='python.exe'", 'get', 'ProcessId,CommandLine'],
            capture_output=True, text=True
        )
        for line in result.stdout.split('\n'):
            if 'app.py' in line and 'signal_dashboard' in line:
                parts = line.strip().split()
                try:
                    pid = int(parts[-1])
                    if pid != my_pid:
                        os.system(f'taskkill /F /PID {pid} 2>nul')
                        print(f'  Killed old process {pid}')
                except:
                    pass
    except:
        pass

    # 等一下让端口释放
    time.sleep(1)

    print("=" * 60)
    print("MA策略信号分析平台 v2")
    print("搜索引擎: MiniMax Web Search")
    print("AI模型: MiniMax M2.7")
    print("访问地址: http://localhost:5000")
    print("=" * 60)

    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
