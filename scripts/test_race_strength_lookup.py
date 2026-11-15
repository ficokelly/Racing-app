#!/usr/bin/env python3
from race_strength_lookup import features,parse_ranked_tier,opposition_features,load_reference

def test(country,title,klass,code,date='2026-11-15',surface='Turf',rtype=''):
 v=features({'jurisdiction':country,'race_name':title,'race_type':rtype,'race_class':klass,'date':date,'surface':surface})
 assert v['classification_code']==code, (country,title,klass,code,v)
 return v

if __name__=='__main__':
 assert len(load_reference())>=30
 test('GB','Champion Hurdle (Grade 1)','Class 1','NH_GRADE_1')
 test('GB','The Derby (Group 1)','Class 1','FLAT_GROUP_1',rtype='Flat')
 test('GB','Novice Hurdle','Class 4','GB_CLASS_4')
 test('GB','Winter Handicap','6','GB_CLASS_6',surface='AW',rtype='Flat')
 test('GB','Greatwood Handicap Hurdle (Premier Handicap)','Class 1','GB_NH_PREMIER_HANDICAP')
 test('GB','Historical Grade 3 Handicap Chase','Class 1','GB_NH_PREMIER_HANDICAP',date='2019-04-01')
 x=test('GB','Grade 3 Handicap Chase','Class 1','GB_CLASS_1')
 assert any('GB_GRADE3_HANDICAP' in s for s in x['warnings'])
 test('IRELAND','Troytown Handicap Chase Grade 3','','IE_NH_G3_HANDICAP')
 test('IRELAND','Mares Handicap Hurdle Listed','','IE_NH_LISTED_HANDICAP')
 test('IRELAND','Irish Derby Group 1','','FLAT_GROUP_1',rtype='Flat')
 test('IRELAND','Premier Handicap 0-100','','IE_FLAT_PREMIER_HANDICAP',rtype='Flat')
 test('IRELAND','0-120 Handicap Hurdle','','IE_NH_BAND_0_120')
 test('IRELAND','0-70 Handicap','','IE_FLAT_BAND_0_70',rtype='Flat')
 test('IRELAND','Maiden Hurdle','','UNKNOWN')
 test('IRELAND','Grade A Handicap Hurdle','','IE_NH_LETTER_A')
 test('IRELAND','Listed NH Flat','','NH_LISTED')
 x=test('GB','Class 5 Handicap Chase','Class 3','UNKNOWN')
 assert 'CONFLICTING_UK_CLASS_TITLE_AND_FIELD' in x['warnings']
 test('GB','Old Class 7 Handicap','Class 7','GB_CLASS_7',date='2019-01-01')
 x=test('IRELAND','Novice Hurdle','Class 4','UNKNOWN');assert any('BRITISH_CLASS' in s for s in x['warnings'])
 x=opposition_features([{'official_rating':120},{'official_rating':115},{'official_rating':110},{'official_rating':105}]);assert x['top4_mean_preoff_or']==112.5
 assert opposition_features([{'or':120},{'or':None},{'or':None}])['field_rating_median'] is None
 print('PASS: 20 classification cases, evidence precedence, UK/Irish historical aliases, OR inputs')
