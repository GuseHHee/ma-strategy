import sqlite3, json, sys, requests
sys.stdout.reconfigure(encoding='utf-8')

db = r'D:\yxw\PY\ma_strategy\signal_dashboard\analysis_history.db'
conn = sqlite3.connect(db)
c = conn.cursor()
c.execute("SELECT id, run_code, signal_date, stock_count, comprehensive_summary, comprehensive_report FROM analysis_runs WHERE run_code='20260515-003'")
run = c.fetchone()
print('Run:', run)
if run:
    run_id = run[0]
    c.execute("SELECT seq, code, name, grade, score, signal_type, analysis, fundamentals FROM analysis_results WHERE run_id=? ORDER BY score DESC, seq", (run_id,))
    rows = c.fetchall()
    print('Results:', len(rows))
    conn.close()
    
    # Get comprehensive from API
    results = []
    for r in rows:
        results.append({
            'code': r[1], 'name': r[2], 'grade': r[3], 'score': r[4],
            'signal_type': r[5], 'analysis': r[6],
            'news_links': [],
            'fundamentals': json.loads(r[7]) if r[7] else {}
        })
    
    payload = {'results': results}
    print('Calling API...')
    resp = requests.post('http://localhost:5000/api/comprehensive', json=payload, timeout=180)
    data = resp.json()
    print('Keys:', list(data.keys()))
    
    with open(r'D:\yxw\PY\ma_strategy\signal_dashboard\_comp_output.txt', 'w', encoding='utf-8') as f:
        f.write('=' * 40 + '\n')
        f.write(f'MA策略综合分析报告 [{run[1]}]\n')
        f.write('=' * 40 + '\n\n')
        f.write(data.get('comprehensive', '') + '\n\n')
        f.write('-' * 40 + '\n')
        f.write('重点排名 TOP10:\n')
        f.write('-' * 40 + '\n')
        for i, r in enumerate(data.get('ranking', [])):
            f.write(f'{i+1}. {r.get("name")}({r.get("code")}) 综合评分{r.get("score")}\n')
        f.write('\n' + '=' * 40 + '\n')
        f.write('个股明细:\n')
        f.write('=' * 40 + '\n')
        for r in results:
            f.write(f'\n【{r["name"]}】{r["code"]} {r["grade"]}级 {r["signal_type"]} 综合评分:{r["score"]}\n')
            f.write(f'{r["analysis"][:300]}...\n')
    
    print('Written to _comp_output.txt')
else:
    conn.close()