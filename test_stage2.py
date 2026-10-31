import csv,json,sqlite3,subprocess,tempfile,unittest
from pathlib import Path
SCRIPT=Path(__file__).with_name('import_historical_ukie.py')
class Stage2Tests(unittest.TestCase):
 def test_import_and_readonly_original(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);csvp=p/'sample.csv'; db=p/'staging.sqlite';report=p/'report.json'
   with csvp.open('w',newline='') as f:
    w=csv.writer(f);w.writerow(['Race Date','Course','Race Number','Horse ID','Horse Name','Finishing Position','SP','Horse Country','Trainer'])
    w.writerow(['2021-02-03','Dundalk','4','old_44','Example Horse (IRE)','1','5/1','IRE','Example Trainer'])
    w.writerow(['2021-02-03','Dundalk','4','old_55','Example Other','2','2/1','GB','Example Trainer'])
    w.writerow(['2026-10-30','Newcastle','6','new_11','Not historical','1','EVS','GB',''])
   proc=subprocess.run(['python',str(SCRIPT),'--input',str(csvp),'--mode','inspect','--report',str(report)],capture_output=True,text=True)
   self.assertEqual(proc.returncode,0,proc.stderr+proc.stdout)
   self.assertFalse(db.exists())
   proc=subprocess.run(['python',str(SCRIPT),'--input',str(csvp),'--mode','import','--database',str(db),'--report',str(report)],capture_output=True,text=True)
   self.assertEqual(proc.returncode,0,proc.stderr+proc.stdout)
   self.assertEqual(sqlite3.connect(db).execute('select count(*) from historical_runs').fetchone()[0],2)
   self.assertEqual(sqlite3.connect(db).execute('select sp_decimal from historical_runs where horse_source_id="old_44"').fetchone()[0],6.0)
   rep=json.loads(report.read_text());self.assertEqual(rep['eligible'],2)
   self.assertEqual(rep['integrity'],'ok')
 def test_unknown_schema_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);csvp=p/'sample.csv';csvp.write_text('hello,world\na,b\n')
   proc=subprocess.run(['python',str(SCRIPT),'--input',str(csvp),'--mode','inspect','--report',str(p/'report.json')],capture_output=True,text=True)
   self.assertNotEqual(proc.returncode,0)
if __name__=='__main__':unittest.main()
