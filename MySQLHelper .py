import pymysql
class MySQLHelper:
    host=""
    username=""
    password=""
    dbname=""
    port=3306
    conn=None
    cursor=None
    def __init__(self,host,username,password,dbname,port=3306):
        self.host=host
        self.username=username
        self.password=password
        self.dbname=dbname
        self.port=port

    def getConnect(self):
        self.conn = pymysql.connect(
            host=self.host,  # 数据库服务器地址
            user=self.username,  # 数据库用户名
            password=self.password,  # 数据库密码
            database=self.dbname,  # 数据库名
            port=self.port,     #数据库端口
            charset='utf8mb4'  # 确保字符集支持
        )
        self.cursor = self.conn.cursor()

    def executeUpdateSQL(self,sql,params=None):
        try:

            self.cursor.execute(sql,params)
            self.conn.commit()
            print("数据更新成功")
        except Exception as e:
            # 错误处理：回滚事务
            print(f"插入数据时发生错误: {e}")
            self.conn.rollback()  # 必要时回滚事务[citation:5]
        finally:
            # 7. 关闭连接
            # self.cursor.close()
            # self.conn.close()  # 关闭数据库连接[citation:5]
            pass

    def executeQuery(self,sql, params=None):
        try:
            self.cursor.execute(sql,params)
            results = self.cursor.fetchall()
            '''if not results:
                print("没有查询到数据")
                return

            print("=== 基础输出 ===")
            for row in results:
                print(row)'''
            return results
        except Exception as e:
            print(f"查询数据时发生错误: {e}")
        finally:
            # 7. 关闭连接
            # self.cursor.close()
            # self.conn.close()  # 关闭数据库连接[citation:5]
            pass

    def close(self):
        self.cursor.close()
        self.conn.close()



if __name__ == '__main__':
    # 请根据实际情况修改以下数据库连接参数
    host=""
    username=""
    password=""
    dbname=""
    port=3306
    dbconn=MySQLHelper(host,username,password,dbname,port)
    dbconn.getConnect()
    count = 100
    sql="insert into news(id,title,publish_time,link_url,news_content,keywords,abstract) " \
        "values(%s,%s,%s,%s,%s,%s,%s)"
    dbconn.close()