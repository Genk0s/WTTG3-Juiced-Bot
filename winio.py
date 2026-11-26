# Windows-only capture, input and raw route recording.
import ctypes as C, ctypes.wintypes as W, time, threading, json
import mss, numpy as np
u=C.WinDLL('user32',use_last_error=True); k=C.WinDLL('kernel32',use_last_error=True)
UP=C.c_size_t; LR=C.c_ssize_t; WP=C.c_size_t; LP=C.c_ssize_t
u.GetForegroundWindow.restype=W.HWND
u.GetClientRect.argtypes=[W.HWND,C.POINTER(W.RECT)];u.ClientToScreen.argtypes=[W.HWND,C.POINTER(W.POINT)]
u.IsWindow.argtypes=[W.HWND]; u.IsIconic.argtypes=[W.HWND]
u.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,C.c_int];u.GetWindowTextW.restype=C.c_int
u.GetAsyncKeyState.argtypes=[C.c_int];u.GetAsyncKeyState.restype=W.SHORT
u.SetCursorPos.argtypes=[C.c_int,C.c_int];u.SetCursorPos.restype=W.BOOL
try:u.SetProcessDpiAwarenessContext(C.c_void_p(-4))
except Exception:
    try:u.SetProcessDPIAware()
    except Exception:pass
class MI(C.Structure):_fields_=[('dx',W.LONG),('dy',W.LONG),('data',W.DWORD),('flags',W.DWORD),('time',W.DWORD),('extra',UP)]
class KI(C.Structure):_fields_=[('vk',W.WORD),('scan',W.WORD),('flags',W.DWORD),('time',W.DWORD),('extra',UP)]
class HI(C.Structure):_fields_=[('msg',W.DWORD),('lo',W.WORD),('hi',W.WORD)]
class IU(C.Union):_fields_=[('mi',MI),('ki',KI),('hi',HI)]
class INPUT(C.Structure):_fields_=[('type',W.DWORD),('u',IU)]
u.SendInput.argtypes=[W.UINT,C.POINTER(INPUT),C.c_int];u.SendInput.restype=W.UINT

def send(i):
    if u.SendInput(1,C.byref(i),C.sizeof(i))!=1:raise RuntimeError('SendInput failed (check elevation / game focus)')
def mouse(dx=0,dy=0,flags=1):
    i=INPUT();i.type=0;i.u.mi=MI(dx,dy,0,flags,0,0);send(i)
def key(scan,down=True,extended=False):
    i=INPUT();i.type=1;i.u.ki=KI(0,scan,8|(0 if down else 2)|(1 if extended else 0),0,0);send(i)
def pressed(vk):return bool(u.GetAsyncKeyState(vk)&0x8000)
STOP=threading.Event()
def watch_stop():
    while True:
        if pressed(0x79):STOP.set()
        time.sleep(.015)
threading.Thread(target=watch_stop,daemon=True).start()
def foreground():return u.GetForegroundWindow()
def title(hwnd):
    b=C.create_unicode_buffer(512);u.GetWindowTextW(hwnd,b,512);return b.value
class Halt(Exception):pass
class Window:
    def __init__(self,hwnd):
        self.hwnd=hwnd;self.sct=mss.mss();self.screen_bounds=dict(self.sct.monitors[0])
        if not pressed(0x79):STOP.clear()
    def check(self):
        if STOP.is_set() or pressed(0x79):raise Halt('F10 stop')
        if foreground()!=self.hwnd or not u.IsWindow(self.hwnd) or u.IsIconic(self.hwnd):raise Halt('Game lost focus or closed')
    def rect(self):
        self.check();r=W.RECT();p=W.POINT(0,0)
        if not u.GetClientRect(self.hwnd,C.byref(r)) or not u.ClientToScreen(self.hwnd,C.byref(p)):raise Halt('Cannot read client rectangle')
        w,h=r.right,r.bottom
        if w<800 or h<450 or abs(w/h-1650/927)>.05:raise Halt('Use a visible 16:9 game window (1920x1080 recommended)')
        m=self.screen_bounds
        if p.x<m['left'] or p.y<m['top'] or p.x+w>m['left']+m['width'] or p.y+h>m['top']+m['height']:raise Halt('Game client is partly outside the screen')
        return p.x,p.y,w,h
    def capture(self):
        x,y,w,h=self.rect();return np.array(self.sct.grab({'left':x,'top':y,'width':w,'height':h}))[:,:,:3].copy()
    def wait(self,seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:self.check();time.sleep(min(.025,max(0,end-time.monotonic())))
        self.check()
    def move(self,x,y):
        l,t,w,h=self.rect()
        if not (0<=x<1650 and 0<=y<927):raise Halt('Move outside game client')
        if not u.SetCursorPos(l+round(x*w/1650),t+round(y*h/927)):raise Halt('SetCursorPos failed')
    def click(self,x,y):
        self.move(x,y)
        self.wait(.045)
        mouse(flags=2)
        try:self.wait(.06)
        finally:mouse(flags=4)
    def click_fast(self,x,y):
        # Short down/up pair; no long sleeps or screen processing here.
        # The window is checked immediately before each individual click.
        self.move(x,y)
        self.check()
        mouse(flags=2)
        try:time.sleep(.030)
        finally:mouse(flags=4)
    def middle_click(self,hold=.12):
        self.check();mouse(flags=0x0020)
        try:self.wait(max(.06,min(.40,float(hold))))
        finally:mouse(flags=0x0040)
    def rapid_click(self,x,y,count,interval):
        self.move(x,y)
        self.wait(.025)
        for _ in range(count):
            self.check()
            mouse(flags=2)
            try:self.wait(.035)
            finally:mouse(flags=4)
            self.wait(max(.025,interval-.035))
    def tap(self,sc):
        self.check();key(sc)
        try:self.wait(.06)
        finally:key(sc,False)
    def type10(self):
        self.check();key(0x1d)
        try:self.tap(0x1e)
        finally:key(0x1d,False)
        self.tap(0x02);self.tap(0x0b)
    def replay(self,events):
        keys=set();buttons=set();start=time.monotonic()
        try:
            for e in events:
                self.wait(max(0,start+e[0]-time.monotonic()));typ=e[1]
                if typ=='m':mouse(e[2],e[3])
                elif typ=='k':
                    sc,down,ext=e[2:];key(sc,down,ext)
                    if down:keys.add((sc,ext))
                    else:keys.discard((sc,ext))
                elif typ=='b':
                    flag=e[2];mouse(flags=flag)
                    if flag==2:buttons.add(4)
                    elif flag==4:buttons.discard(4)
        finally:
            for sc,ext in keys:key(sc,False,ext)
            for flag in buttons:mouse(flags=flag)
# Raw Input: receives relative mouse movement even when UE locks the cursor.
class RID(C.Structure):_fields_=[('page',W.USHORT),('usage',W.USHORT),('flags',W.DWORD),('target',W.HWND)]
class RH(C.Structure):_fields_=[('type',W.DWORD),('size',W.DWORD),('device',W.HANDLE),('param',WP)]
class RM(C.Structure):_fields_=[('flags',W.USHORT),('buttons',W.ULONG),('raw',W.ULONG),('dx',W.LONG),('dy',W.LONG),('extra',W.ULONG)]
class RK(C.Structure):_fields_=[('scan',W.USHORT),('flags',W.USHORT),('reserved',W.USHORT),('vk',W.USHORT),('msg',W.UINT),('extra',W.ULONG)]
PROC=C.WINFUNCTYPE(LR,W.HWND,W.UINT,WP,LP)
class WC(C.Structure):_fields_=[('style',W.UINT),('proc',PROC),('cbClsExtra',C.c_int),('cbWndExtra',C.c_int),('instance',W.HINSTANCE),('icon',W.HICON),('cursor',W.HANDLE),('background',W.HBRUSH),('menu',W.LPCWSTR),('name',W.LPCWSTR)]
u.DefWindowProcW.argtypes=[W.HWND,W.UINT,WP,LP];u.DefWindowProcW.restype=LR
u.RegisterClassW.argtypes=[C.POINTER(WC)];u.RegisterClassW.restype=W.ATOM
u.CreateWindowExW.argtypes=[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,C.c_void_p];u.CreateWindowExW.restype=W.HWND
u.RegisterRawInputDevices.argtypes=[C.POINTER(RID),W.UINT,W.UINT];u.RegisterRawInputDevices.restype=W.BOOL
u.GetRawInputData.argtypes=[W.HANDLE,W.UINT,C.c_void_p,C.POINTER(W.UINT),W.UINT];u.GetRawInputData.restype=W.UINT
u.PostMessageW.argtypes=[W.HWND,W.UINT,WP,LP];u.DestroyWindow.argtypes=[W.HWND]
k.GetModuleHandleW.argtypes=[W.LPCWSTR];k.GetModuleHandleW.restype=W.HMODULE
class Recorder:
    def __init__(self,target):
        self.target=target;self.events=[];self.start=time.monotonic();self.active=True;self.error=None;self.ready=threading.Event();self.hwnd=None
        self.thread=threading.Thread(target=self.worker,daemon=True);self.thread.start()
        if not self.ready.wait(4) or self.error:raise RuntimeError(self.error or 'Raw Input timeout')
    def stop(self):
        self.active=False
        if self.hwnd:u.PostMessageW(self.hwnd,0x10,0,0)
        self.thread.join(2);return self.events
    def worker(self):
        allowed={0x11,0x1e,0x1f,0x20,0x12,0x21,0x2a,0x36,0x39,0x1d} # W,A,S,D,E,F,Shift,Space,Ctrl
        def cb(hwnd,msg,wp,lp):
            try:
                if msg==0x00ff and self.active and foreground()==self.target:
                    size=W.UINT();u.GetRawInputData(C.c_void_p(lp),0x10000003,None,C.byref(size),C.sizeof(RH));buf=C.create_string_buffer(size.value)
                    if u.GetRawInputData(C.c_void_p(lp),0x10000003,buf,C.byref(size),C.sizeof(RH))==size.value:
                        head=RH.from_buffer_copy(buf);ts=round(time.monotonic()-self.start,4);off=C.sizeof(RH)
                        if head.type==0:
                            m=RM.from_buffer_copy(buf,off)
                            if not (m.flags&1) and (m.dx or m.dy):self.events.append([ts,'m',m.dx,m.dy])
                            for b,f in [(1,2),(2,4)]:
                                if m.buttons&b:self.events.append([ts,'b',f])
                        elif head.type==1:
                            q=RK.from_buffer_copy(buf,off)
                            if q.scan in allowed:self.events.append([ts,'k',q.scan,not bool(q.flags&1),bool(q.flags&2)])
                elif msg==0x10:u.DestroyWindow(hwnd);return 0
                elif msg==2:u.PostQuitMessage(0);return 0
            except Exception as e:self.error=str(e);self.active=False
            return u.DefWindowProcW(hwnd,msg,wp,lp)
        try:
            self.cb=PROC(cb);name=f'WTTG3Raw{time.time_ns()}';inst=k.GetModuleHandleW(None)
            wc=WC();wc.proc=self.cb;wc.instance=inst;wc.name=name
            if not u.RegisterClassW(C.byref(wc)):raise RuntimeError('RegisterClass failed')
            self.hwnd=u.CreateWindowExW(0,name,name,0,0,0,0,0,None,None,inst,None)
            if not self.hwnd:raise RuntimeError('CreateWindow failed')
            dev=(RID*2)(RID(1,2,0x100,self.hwnd),RID(1,6,0x100,self.hwnd))
            if not u.RegisterRawInputDevices(dev,2,C.sizeof(RID)):raise RuntimeError('RegisterRawInputDevices failed')
            self.start=time.monotonic();self.ready.set();m=W.MSG()
            while u.GetMessageW(C.byref(m),None,0,0)>0:u.TranslateMessage(C.byref(m));u.DispatchMessageW(C.byref(m))
        except Exception as e:self.error=str(e);self.ready.set()
