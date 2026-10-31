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
 def test_kaggle_actual_schema(self):
  # Real column names taken from the validated 31 Oct inspection report.
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);csvp=p/'raceform.csv';db=p/'hist.sqlite';report=p/'report.json'
   header=['date','course','race_id','off','race_name','type','class','pattern','rating_band','age_band','sex_rest','dist','going','ran','num','pos','draw','ovr_btn','btn','horse','age','sex','wgt','hg','time','sp','jockey','trainer','prize','or','rpr','ts','sire','dam','damsire','owner','comment']
   sample={'date':'2021-06-17','course':'Ascot','race_id':'123456','off':'14:15','type':'Flat','class':'2','dist':'1m 2f 110y','going':'Good','pos':'1','draw':'6','btn':'0','horse':'Example Runner (IRE)','age':'4','sex':'g','wgt':'9-4','time':'2m 3.1s','sp':'10/1','jockey':'Rider','trainer':'Yard','or':'92','rpr':'108','ts':'93','sire':'Example Sire','dam':'Example Dam','comment':'Led late'}
   with csvp.open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=header);w.writeheader();w.writerow(sample)
   proc=subprocess.run(['python',str(SCRIPT),'--input',str(csvp),'--mode','import','--database',str(db),'--report',str(report)],capture_output=True,text=True)
   self.assertEqual(proc.returncode,0,proc.stderr+proc.stdout)
   c=sqlite3.connect(db)
   self.assertEqual(c.execute('select distance_f from historical_races').fetchone()[0],10.5)
   data=c.execute('select weight_lbs,official_rating,rpr,topspeed,sp_decimal,race_comment from historical_runs').fetchone()
   self.assertEqual(data,(130.0,92.0,108.0,93.0,11.0,'Led late'))
   self.assertEqual(json.loads(report.read_text())['integrity'],'ok')

 def test_unknown_schema_rejected(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);csvp=p/'sample.csv';csvp.write_text('hello,world\na,b\n')
   proc=subprocess.run(['python',str(SCRIPT),'--input',str(csvp),'--mode','inspect','--report',str(p/'report.json')],capture_output=True,text=True)
   self.assertNotEqual(proc.returncode,0)
if __name__=='__main__':unittest.main()
