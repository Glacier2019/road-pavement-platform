from rpdao import Dao
d = Dao().open()
fs = d.design_files_received()
print("收到文件数:", len(fs))
print("状态分布:", d.design_parse_status_counts())
sql = "select file_name, parse_status from design_file where file_name ilike %(p)s"
for row in d.query(sql, {"p": "%.tsf%"}):
    print("  TSF 记录:", row)
d.close()