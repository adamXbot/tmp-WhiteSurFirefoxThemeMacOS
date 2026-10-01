"""Temporary, focused review of open theme PRs on current Firefox."""
import argparse
import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from fxcss import core, install

PROBE = """
const w=Services.wm.getMostRecentWindow('navigator:browser'),d=w.document;
function el(e) {
 if(!e)return null;
 const s=w.getComputedStyle(e),r=e.getBoundingClientRect();
 return {width:r.width,x:r.x,y:r.y,h:r.height,display:s.display,visibility:s.visibility,selected:e.hasAttribute('visuallyselected'),multiselected:e.hasAttribute('multiselected'),maxWidth:s.maxWidth,marginEnd:s.marginInlineEnd,color:s.backgroundColor,order:s.order};
}
return {version:Services.appinfo.version,os:Services.appinfo.OS,orient:d.getElementById('tabbrowser-tabs').getAttribute('orient'),group:w._reviewGroup ? {collapsed:w._reviewGroup.collapsed,tabs:Array.from(w._reviewGroup.tabs).map(el)}:null,pinned:el(d.getElementById('pinned-tabs-container')),nav:el(d.getElementById('nav-bar')),url:el(d.getElementById('urlbar')),toolbar:el(d.getElementById('TabsToolbar')),controls:el(d.querySelector('#TabsToolbar .titlebar-buttonbox-container')),close:el(d.querySelector('#TabsToolbar .titlebar-close')),palette:w.getComputedStyle(d.documentElement).getPropertyValue('--gnome-toolbar-background').trim()};
"""

def js(session, code):
    return session.m.script("const w=Services.wm.getMostRecentWindow('navigator:browser');"+code)

def measure_change(session, code):
    # Measure within the same automation command: a subsequent chrome command
    # can restore Marionette's tracked content tab and change selection again.
    return session.m.async_script("const done=arguments[arguments.length-1];const w=Services.wm.getMostRecentWindow('navigator:browser');"+code+"w.setTimeout(()=>done((function(){"+PROBE+"})()),700);")

def group_states(session, out, name):
    js(session,"const tabs=Array.from(w.gBrowser.tabs).filter(t=>!t.pinned);w._reviewOutside=tabs[2];w._reviewGroup=w.gBrowser.addTabGroup([tabs[0],tabs[1]],{label:'Review',color:'blue'});")
    time.sleep(.4)
    states={'collapsed':measure_change(session,"w.gBrowser.selectedTab=w._reviewOutside;w._reviewGroup.collapsed=true;")}
    (out/(name+'-collapsed.png')).write_bytes(session.m.screenshot())
    states['expanded']=measure_change(session,"w._reviewGroup.collapsed=false;")
    states['active']=measure_change(session,"w.gBrowser.selectedTab=w._reviewGroup.tabs[0];w._reviewGroup.collapsed=true;")
    (out/(name+'-active.png')).write_bytes(session.m.screenshot())
    # Cascade guard test: Firefox's collapse action clears multi-selection,
    # so exercise the multiselected attribute after the collapse transition.
    states['multiselected-attribute']=measure_change(session,"w._reviewGroup.tabs[1].setAttribute('multiselected','true');")
    js(session,"w._reviewGroup.tabs[1].removeAttribute('multiselected');w._reviewGroup.collapsed=false;w.gBrowser.selectedTab=w._reviewOutside;")
    session.m.script(core.ENABLE_VERTICAL_TABS)
    for _ in range(50):
        if session.m.script(PROBE)['orient']=='vertical':break
        time.sleep(.1)
    states['vertical']=measure_change(session,"w.gBrowser.selectedTab=w._reviewOutside;w._reviewGroup.collapsed=true;")
    (out/(name+'-vertical.png')).write_bytes(session.m.screenshot())
    return states

def copy_theme(base,destination):
    destination.mkdir()
    for name in ('chrome','custom','configuration'):
        shutil.copytree(base/name,destination/name)

def run(args):
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    results=[]
    with tempfile.TemporaryDirectory(prefix='whitesur-pr-review-') as td:
        temp=Path(td)
        for name in ('base','pr187','pr170','monterey-original','monterey-palette-fix'):
            copy_theme(args.base,temp/name)
        p=temp/'pr187/chrome/WhiteSur/parts/tabsbar.css'
        source=(args.pr187/'chrome/WhiteSur/parts/tabsbar.css').read_text()
        rule=re.search(r'#tabbrowser-tabs\[orient="horizontal"\] tab-group\[collapsed\] > \.tabbrowser-tab:not\(\[visuallyselected\]\):not\(\[multiselected\]\)\s*\{[^}]+\}',source).group()
        p.write_text(p.read_text()+'\n'+rule+'\n')
        p=temp/'pr170/chrome/WhiteSur/parts/tabsbar.css'
        source=(args.pr170/'chrome/WhiteSur/parts/tabsbar.css').read_text()
        rule=re.search(r'#pinned-tabs-container\[orient="horizontal"\]\s*\{[^}]+\}',source).group()
        p.write_text(p.read_text()+'\n'+rule+'\n')
        for name in ('monterey-original','monterey-palette-fix'):
            shutil.copytree(args.pr194/'chrome/Monterey',temp/name/'chrome/Monterey')
        p=temp/'monterey-palette-fix/chrome/Monterey/theme.css'
        p.write_text(p.read_text().replace('@import "custom/theme-','@import "../WhiteSur/custom/theme-'))
        p=temp/'monterey-palette-fix/chrome/Monterey/parts/tabsbar.css'
        p.write_text(p.read_text().replace('&[collapsed] > .tabbrowser-tab {','&[collapsed] > .tabbrowser-tab:not([visuallyselected]):not([multiselected]) {'))
        for name in ('base','pr187','pr170','monterey-original','monterey-palette-fix'):
            theme=temp/name
            record={'theme':name}
            profile=temp/(name+'-installed');profile.mkdir()
            manifest=install.install_theme(theme,profile,'review',sheets=[theme/'custom/theme-github-dark.css'])
            record['paletteFiles']=[p for p in manifest['files'] if p.endswith('/theme-github-dark.css')]
            with core.Session(profile,core.find_firefox(),dark=True) as s:
                s.setup_window(pinned=True);time.sleep(.7)
                record['defaultPalette']=s.m.script(PROBE)
                (out/(name+'-default-palette.png')).write_bytes(s.m.screenshot())
            if name.startswith('monterey'):
                entry=theme/'chrome/userChrome.css'
                entry.write_text(entry.read_text().replace('WhiteSur/theme.css','Monterey/theme.css'))
            with core.Session(theme,core.find_firefox(),dark=True) as s:
                s.setup_window(pinned=True);time.sleep(.7)
                record['initial']=s.m.script(PROBE)
                (out/(name+'-initial.png')).write_bytes(s.m.screenshot())
                if name!='pr170':
                    record['states']=group_states(s,out,name)
                if name.startswith('monterey'):
                    js(s,"Services.prefs.setBoolPref('sidebar.verticalTabs',false);w._reviewGroup.collapsed=false;")
                    time.sleep(.7)
                    s.m.script(core.RESIZE,[800,600]);time.sleep(.7)
                    record['narrow']=s.m.script(PROBE)
                    (out/(name+'-narrow.png')).write_bytes(s.m.screenshot())
                if name=='base':
                    uri=(theme/'custom/windows-swapclose.css').resolve().as_uri()
                    js(s,"Services.prefs.setBoolPref('sidebar.verticalTabs',false);")
                    time.sleep(.5)
                    before=s.m.script(PROBE)
                    s.apply_css('@import '+json.dumps(uri)+';')
                    time.sleep(.5)
                    record['swapclose']={'before':before,'after':s.m.script(PROBE)}
            results.append(record)
            (out/'results.json').write_text(json.dumps(results,indent=2)+'\n')
            print(name,record['paletteFiles'],flush=True)
        records={r['theme']:r for r in results}
        fixed=records['pr187']['states']
        assert any(not t['selected'] for t in fixed['collapsed']['group']['tabs'])
        assert all(t['width']==0 for t in fixed['collapsed']['group']['tabs'] if not t['selected']),fixed['collapsed']
        assert all(t['width']>0 for t in fixed['expanded']['group']['tabs']),fixed['expanded']
        assert fixed['active']['group']['tabs'][0]['width']>0 and fixed['active']['group']['tabs'][1]['width']==0,fixed['active']
        assert all(t['width']>0 for t in fixed['multiselected-attribute']['group']['tabs']),fixed['multiselected-attribute']
        assert fixed['vertical']['orient']=='vertical'
        assert all(t['display']=='none' for t in fixed['vertical']['group']['tabs'] if not t['selected']),fixed['vertical']
        assert records['base']['initial']['pinned']['marginEnd']==records['pr170']['initial']['pinned']['marginEnd']=='0px'
        assert records['monterey-original']['paletteFiles']==['chrome/Monterey/custom/theme-github-dark.css']
        assert records['monterey-palette-fix']['paletteFiles']==['chrome/WhiteSur/custom/theme-github-dark.css']
        assert records['monterey-original']['states']['active']['group']['tabs'][0]['width']==0
        assert records['monterey-palette-fix']['states']['active']['group']['tabs'][0]['width']>0
        print('PASS: PR187 collapse/expand/active/multi-selection/vertical checks; PR170 zero margin; PR194 palette regression and correction.',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for key in ('base','pr187','pr170','pr194','out'):
        parser.add_argument('--'+key,type=Path,required=True)
    run(parser.parse_args())
