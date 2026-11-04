import sqlite3

def safe_query(username: str):
    """✅ 参数化查询，安全，不会注入"""
    conn = sqlite3.connect("demo.db")
    cur = conn.cursor()
    # ? 占位符，不要字符串拼接！
    sql = "SELECT id, amount, status FROM orders WHERE username = ?"
    cur.execute(sql, (username, ))
    rows = cur.fetchall()
    conn.close()
    return rows

def unsafe_bad_demo(username: str):
    """❌ 禁止！字符串拼接，注入漏洞，政企红线"""
    # sql = f"SELECT * FROM orders WHERE username = '{username}'"
    pass

if __name__ == "__main__":
    # 建测试表
    conn = sqlite3.connect("demo.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS orders
                 (id INTEGER PRIMARY KEY, username TEXT, amount REAL, status TEXT)''')
    c.execute("INSERT INTO orders(username, amount, status) VALUES (?,?,?)", ("zhangsan", 2000, "done"))
    conn.commit()
    conn.close()

    res = safe_query("zhangsan")
    print(res)