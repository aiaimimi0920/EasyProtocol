package sentinel

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/rand"
	"os"
	"regexp"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

const (
	turnstileQueueReg    = 9
	turnstileWindowReg   = 10
	turnstileKeyReg      = 16
	turnstileSuccessReg  = 3
	turnstileErrorReg    = 4
	turnstileCallbackReg = 30
	orderedKeysMeta      = "__ordered_keys__"
	ownPropertyNamesMeta = "__own_property_names__"
	prototypeMeta        = "__prototype__"
	callMeta             = "__call__"
	functionNameMeta     = "__function_name__"
	functionSourceMeta   = "__function_source__"
	objectNameMeta       = "__object_name__"
)

type vmFunc func(args ...any) (any, error)

type nativeVMFunc struct {
	name string
	fn   vmFunc
}

type stringifiedVMFunc struct {
	source string
	fn     vmFunc
}

type regMapRef struct {
	solver *turnstileSolver
}

type turnstileSolver struct {
	session   *Session
	profile   turnstileRequirementsProfile
	regs      map[string]any
	window    map[string]any
	done      bool
	resolved  string
	rejected  string
	stepCount int
	maxSteps  int
	traceEnabled bool
	traceProps   map[string]int
	traceCalls   map[string]int
	traceLog     []string
	objectSeq    int
}

func solveTurnstileDX(requirementsToken, dx string) (string, error) {
	return solveTurnstileDXWithSession(requirementsToken, dx, nil)
}

func solveTurnstileDXWithSession(requirementsToken, dx string, session *Session) (string, error) {
	profile, _ := parseTurnstileRequirementsProfile(requirementsToken)
	solver := &turnstileSolver{
		session:      session,
		profile:      profile,
		maxSteps:     50000,
		traceEnabled: strings.TrimSpace(os.Getenv("TURNSTILE_VM_TRACE")) == "1",
		traceProps:   map[string]int{},
		traceCalls:   map[string]int{},
	}
	return solver.solve(requirementsToken, dx)
}

func nativeVMFuncNamed(name string, fn vmFunc) nativeVMFunc {
	return nativeVMFunc{
		name: strings.TrimSpace(name),
		fn:   fn,
	}
}

func stringifiedVMFuncSource(source string, fn vmFunc) stringifiedVMFunc {
	return stringifiedVMFunc{
		source: strings.TrimSpace(source),
		fn:     fn,
	}
}

func callableObject(name string, props map[string]any, call vmFunc) map[string]any {
	value := withOrderedKeys(props, []string{})
	value[functionNameMeta] = strings.TrimSpace(name)
	if _, exists := value["length"]; !exists {
		value["length"] = float64(0)
	}
	if _, exists := value["name"]; !exists {
		value["name"] = strings.TrimSpace(name)
	}
	if _, exists := value["prototype"]; !exists {
		value["prototype"] = withOrderedKeys(map[string]any{}, []string{})
	}
	if call != nil {
		value[callMeta] = nativeVMFuncNamed(name, call)
	}
	return value
}

func namedObject(name string, props map[string]any, keys []string) map[string]any {
	value := withOrderedKeys(props, keys)
	value[objectNameMeta] = strings.TrimSpace(name)
	return value
}

func (s *turnstileSolver) traceProp(obj any, prop string) {
	if !s.traceEnabled {
		return
	}
	objMap, ok := obj.(map[string]any)
	if !ok {
		return
	}
	name := strings.TrimSpace(jsonString(objMap[objectNameMeta]))
	if name == "" {
		return
	}
	prop = strings.TrimSpace(prop)
	if prop == "" || isInternalMetaKey(prop) {
		return
	}
	s.traceProps[name+"."+prop]++
}

func (s *turnstileSolver) traceCall(label string) {
	if !s.traceEnabled {
		return
	}
	label = strings.TrimSpace(label)
	if label == "" {
		return
	}
	s.traceCalls[label]++
	if len(s.traceLog) < 256 {
		s.traceLog = append(s.traceLog, label)
	}
}

func (s *turnstileSolver) traceTargetCall(base string, target any) {
	if !s.traceEnabled {
		return
	}
	label := strings.TrimSpace(base)
	switch value := target.(type) {
	case map[string]any:
		if name := strings.TrimSpace(jsonString(value[objectNameMeta])); name != "" {
			label += "(" + name + ")"
		}
	case []any:
		label += "([array])"
	case []string:
		label += "([string-array])"
	case string:
		label += "(string)"
	case nil:
		label += "(null)"
	}
	s.traceCall(label)
}

func (s *turnstileSolver) traceDetail(base string, detail any) {
	if !s.traceEnabled {
		return
	}
	label := strings.TrimSpace(base)
	detailText := ""
	switch value := detail.(type) {
	case map[string]any, []any, []string:
		if encoded, err := json.Marshal(detail); err == nil {
			detailText = string(encoded)
		}
	default:
		detailText = strings.TrimSpace(s.jsToString(value))
	}
	if detailText != "" {
		if len(detailText) > 180 {
			detailText = detailText[:180] + "…"
		}
		label += "=" + detailText
	}
	s.traceCall(label)
}

func (s *turnstileSolver) dumpTrace() {
	if !s.traceEnabled {
		return
	}
	propPairs := make([]string, 0, len(s.traceProps))
	for key, count := range s.traceProps {
		propPairs = append(propPairs, fmt.Sprintf("%s=%d", key, count))
	}
	sort.Strings(propPairs)
	callPairs := make([]string, 0, len(s.traceCalls))
	for key, count := range s.traceCalls {
		callPairs = append(callPairs, fmt.Sprintf("%s=%d", key, count))
	}
	sort.Strings(callPairs)
	fmt.Fprintf(os.Stderr, "[turnstile-vm-trace] props=%s\n", strings.Join(propPairs, ","))
	fmt.Fprintf(os.Stderr, "[turnstile-vm-trace] calls=%s\n", strings.Join(callPairs, ","))
	if len(s.traceLog) > 0 {
		fmt.Fprintf(os.Stderr, "[turnstile-vm-trace] seq=%s\n", strings.Join(s.traceLog, " | "))
	}
}

type turnstileRequirementsProfile struct {
	ScreenSum           int
	HeapLimit           int64
	UserAgent           string
	ScriptURL           string
	Language            string
	LanguagesJoin       string
	NavigatorProbe      string
	DocumentProbe       string
	WindowProbe         string
	PerformanceNow      float64
	SessionID           string
	HardwareConcurrency int
	TimeOrigin          float64
}

var authWindowKeyOrder = strings.Split(`
onmouseover,close,0,window,self,document,name,location,customElements,history,navigation,locationbar,menubar,personalbar,scrollbars,statusbar,toolbar,status,closed,frames,length,top,opener,parent,frameElement,navigator,origin,external,screen,innerWidth,innerHeight,scrollX,pageXOffset,scrollY,pageYOffset,visualViewport,screenX,screenY,outerWidth,outerHeight,devicePixelRatio,event,clientInformation,screenLeft,screenTop,styleMedia,onsearch,onappinstalled,onbeforeinstallprompt,onabort,onbeforeinput,onbeforematch,onbeforetoggle,onblur,oncancel,oncanplay,oncanplaythrough,onchange,onclick,onclose,oncommand,oncontentvisibilityautostatechange,oncontextlost,oncontextmenu,oncontextrestored,oncuechange,ondblclick,ondrag,ondragend,ondragenter,ondragleave,ondragover,ondragstart,ondrop,ondurationchange,onemptied,onended,onerror,onfocus,onformdata,oninput,oninvalid,onkeydown,onkeypress,onkeyup,onload,onloadeddata,onloadedmetadata,onloadstart,onmousedown,onmouseenter,onmouseleave,onmousemove,onmouseout,onmouseup,onmousewheel,onpause,onplay,onplaying,onprogress,onratechange,onreset,onresize,onscroll,onscrollend,onsecuritypolicyviolation,onseeked,onseeking,onselect,onslotchange,onstalled,onsubmit,onsuspend,ontimeupdate,ontoggle,onvolumechange,onwaiting,onwebkitanimationend,onwebkitanimationiteration,onwebkitanimationstart,onwebkittransitionend,onwheel,onauxclick,ongotpointercapture,onlostpointercapture,onpointerdown,onpointermove,onpointerup,onpointercancel,onpointerover,onpointerout,onpointerenter,onpointerleave,onselectstart,onselectionchange,onanimationcancel,onanimationend,onanimationiteration,onanimationstart,ontransitionrun,ontransitionstart,ontransitionend,ontransitioncancel,onbeforexrselect,onafterprint,onbeforeprint,onbeforeunload,onhashchange,onlanguagechange,onmessage,onmessageerror,onoffline,ononline,onpagehide,onpageshow,onpopstate,onrejectionhandled,onstorage,onunhandledrejection,onunload,isSecureContext,crossOriginIsolated,scheduler,performance,trustedTypes,crypto,indexedDB,localStorage,sessionStorage,alert,atob,blur,btoa,cancelAnimationFrame,cancelIdleCallback,captureEvents,clearInterval,clearTimeout,confirm,createImageBitmap,fetch,find,focus,getComputedStyle,getSelection,matchMedia,moveBy,moveTo,open,postMessage,print,prompt,queueMicrotask,releaseEvents,reportError,requestAnimationFrame,requestIdleCallback,resizeBy,resizeTo,scroll,scrollBy,scrollTo,setInterval,setTimeout,stop,structuredClone,webkitCancelAnimationFrame,webkitRequestAnimationFrame,chrome,crashReport,cookieStore,ondevicemotion,ondeviceorientation,ondeviceorientationabsolute,onpointerrawupdate,caches,documentPictureInPicture,sharedStorage,fetchLater,getScreenDetails,queryLocalFonts,showDirectoryPicker,showOpenFilePicker,showSaveFilePicker,originAgentCluster,viewport,onpageswap,onpagereveal,credentialless,fence,launchQueue,speechSynthesis,onscrollsnapchange,onscrollsnapchanging,ongamepadconnected,ongamepaddisconnected,webkitRequestFileSystem,webkitResolveLocalFileSystemURL,cdc_adoQpoasnfa76pfcZLmcfl_Array,cdc_adoQpoasnfa76pfcZLmcfl_Object,cdc_adoQpoasnfa76pfcZLmcfl_Promise,cdc_adoQpoasnfa76pfcZLmcfl_Proxy,cdc_adoQpoasnfa76pfcZLmcfl_Symbol,cdc_adoQpoasnfa76pfcZLmcfl_JSON,cdc_adoQpoasnfa76pfcZLmcfl_Window,__reactRouterContext,$RB,$RV,$RC,$RT,ret_nodes,__reactRouterManifest,__STATSIG__,__reactRouterVersion,__REACT_INTL_CONTEXT__,DD_RUM,__SEGMENT_INSPECTOR__,__reactRouterRouteModules,__reactRouterDataRouter,__sentinel_token_pending,__sentinel_init_pending,SentinelSDK
`, ",")

var authNavigatorPrototypeKeys = []string{
	"productSub", "canLoadAdAuctionFencedFrame", "vendorSub", "vendor", "maxTouchPoints", "scheduling", "userActivation", "geolocation", "doNotTrack",
	"webkitTemporaryStorage", "webkitPersistentStorage", "hardwareConcurrency", "cookieEnabled", "appCodeName", "appName", "appVersion", "platform", "product", "userAgent",
	"language", "languages", "onLine", "plugins", "mimeTypes", "pdfViewerEnabled", "connection",
	"getGamepads", "javaEnabled", "sendBeacon", "vibrate",
	"windowControlsOverlay", "deprecatedRunAdAuctionEnforcesKAnonymity", "protectedAudience", "bluetooth",
	"clipboard", "credentials", "keyboard", "managed", "mediaDevices", "serviceWorker",
	"virtualKeyboard", "wakeLock", "deviceMemory", "userAgentData", "locks", "storage", "gpu", "login", "ink", "mediaCapabilities",
	"devicePosture", "hid", "mediaSession", "permissions", "presentation", "serial", "usb", "xr", "storageBuckets",
	"adAuctionComponents", "runAdAuction", "canShare", "share", "clearAppBadge",
	"getBattery", "getUserMedia", "requestMIDIAccess", "requestMediaKeySystemAccess", "setAppBadge", "webkitGetUserMedia",
	"clearOriginJoinedAdInterestGroups", "createAuctionNonce", "joinAdInterestGroup", "leaveAdInterestGroup",
	"updateAdInterestGroups", "deprecatedReplaceInURN", "deprecatedURNToURL", "getInstalledRelatedApps",
	"getInterestGroupAdAuctionData", "registerProtocolHandler", "unregisterProtocolHandler",
}

var authNavigatorKeyOrder = strings.Split(`
productSub,canLoadAdAuctionFencedFrame,vendorSub,vendor,maxTouchPoints,scheduling,userActivation,geolocation,doNotTrack,webkitTemporaryStorage,webkitPersistentStorage,hardwareConcurrency,cookieEnabled,appCodeName,appName,appVersion,platform,product,userAgent,language,languages,onLine,plugins,mimeTypes,pdfViewerEnabled,connection,getGamepads,javaEnabled,sendBeacon,vibrate,windowControlsOverlay,deprecatedRunAdAuctionEnforcesKAnonymity,protectedAudience,bluetooth,clipboard,credentials,keyboard,managed,mediaDevices,serviceWorker,virtualKeyboard,wakeLock,deviceMemory,userAgentData,locks,storage,gpu,login,ink,mediaCapabilities,devicePosture,hid,mediaSession,permissions,presentation,serial,usb,xr,storageBuckets,adAuctionComponents,runAdAuction,canShare,share,clearAppBadge,getBattery,getUserMedia,requestMIDIAccess,requestMediaKeySystemAccess,setAppBadge,webkitGetUserMedia,clearOriginJoinedAdInterestGroups,createAuctionNonce,joinAdInterestGroup,leaveAdInterestGroup,updateAdInterestGroups,deprecatedReplaceInURN,deprecatedURNToURL,getInstalledRelatedApps,getInterestGroupAdAuctionData,registerProtocolHandler,unregisterProtocolHandler,webdriver
`, ",")

var authNavigatorPrototypeOwnPropertyNames = []string{
	"productSub", "canLoadAdAuctionFencedFrame", "vendorSub", "vendor", "maxTouchPoints", "scheduling", "userActivation", "geolocation", "doNotTrack",
	"webkitTemporaryStorage", "webkitPersistentStorage", "hardwareConcurrency", "cookieEnabled", "appCodeName", "appName", "appVersion", "platform", "product", "userAgent",
	"language", "languages", "onLine", "plugins", "mimeTypes", "pdfViewerEnabled", "connection",
	"getGamepads", "javaEnabled", "sendBeacon", "vibrate", "windowControlsOverlay", "constructor",
	"deprecatedRunAdAuctionEnforcesKAnonymity", "protectedAudience", "bluetooth", "clipboard", "credentials", "keyboard", "managed", "mediaDevices",
	"serviceWorker", "virtualKeyboard", "wakeLock", "deviceMemory", "userAgentData", "locks", "storage", "gpu", "login", "ink", "mediaCapabilities",
	"devicePosture", "hid", "mediaSession", "permissions", "presentation", "serial", "usb", "xr", "storageBuckets",
}

var authWindowOwnPropertyNames = []string{
	"onmouseover", "close", "0", "Object", "Function", "Array", "Number", "parseFloat", "parseInt", "Infinity", "NaN", "undefined", "Boolean", "String", "Symbol",
	"Date", "Promise", "RegExp", "Error", "AggregateError", "EvalError", "RangeError", "ReferenceError", "SyntaxError", "TypeError", "URIError", "globalThis", "JSON",
	"Math", "Intl", "ArrayBuffer", "Atomics", "Uint8Array", "Int8Array", "Uint16Array", "Int16Array", "Uint32Array", "Int32Array", "BigUint64Array", "BigInt64Array",
	"Uint8ClampedArray", "Float32Array", "Float64Array", "DataView", "Map", "BigInt", "Set", "Iterator", "WeakMap", "WeakSet", "Proxy", "Reflect", "FinalizationRegistry",
	"WeakRef", "decodeURI", "decodeURIComponent", "encodeURI", "encodeURIComponent", "escape", "unescape", "eval", "isFinite", "isNaN", "console", "Option", "Image", "Audio",
	"webkitURL", "webkitRTCPeerConnection", "webkitMediaStream", "WebKitMutationObserver", "WebKitCSSMatrix", "XPathResult", "XPathExpression", "XPathEvaluator", "XMLSerializer",
	"XMLHttpRequestUpload", "XMLHttpRequestEventTarget", "XMLHttpRequest", "XMLDocument",
}

var authPerformancePrototypeOwnPropertyNames = []string{
	"timeOrigin", "onresourcetimingbufferfull", "clearMarks", "clearMeasures", "clearResourceTimings", "getEntries", "getEntriesByName", "getEntriesByType", "mark", "measure",
	"setResourceTimingBufferSize", "toJSON", "now", "constructor", "timing", "navigation", "memory", "eventCounts", "interactionCount",
}

func parseTurnstileRequirementsProfile(requirementsToken string) (turnstileRequirementsProfile, error) {
	var profile turnstileRequirementsProfile
	token := strings.TrimSpace(requirementsToken)
	token = strings.TrimPrefix(token, "gAAAAAC")
	token = strings.TrimSuffix(token, "~S")
	if token == "" {
		return profile, fmt.Errorf("empty requirements token")
	}
	body, err := base64.StdEncoding.DecodeString(token)
	if err != nil {
		return profile, err
	}
	var fields []any
	if err := json.Unmarshal(body, &fields); err != nil {
		return profile, err
	}
	if len(fields) < 18 {
		return profile, fmt.Errorf("invalid requirements field count: %d", len(fields))
	}
	profile.ScreenSum = int(jsonFloat(fields[0]))
	profile.HeapLimit = int64(jsonFloat(fields[2]))
	profile.UserAgent = jsonString(fields[4])
	profile.ScriptURL = jsonString(fields[5])
	profile.Language = jsonString(fields[7])
	profile.LanguagesJoin = jsonString(fields[8])
	profile.NavigatorProbe = jsonString(fields[10])
	profile.DocumentProbe = jsonString(fields[11])
	profile.WindowProbe = jsonString(fields[12])
	profile.PerformanceNow = jsonFloat(fields[13])
	profile.SessionID = jsonString(fields[14])
	profile.HardwareConcurrency = int(jsonFloat(fields[16]))
	profile.TimeOrigin = jsonFloat(fields[17])
	return profile, nil
}

func chromeVersionHints(userAgent string) (string, string) {
	major := "147"
	full := "147.0.0.0"
	ua := strings.TrimSpace(userAgent)
	if ua == "" {
		return major, full
	}
	re := regexp.MustCompile(`Chrome/([0-9]+(?:\.[0-9]+){0,3})`)
	match := re.FindStringSubmatch(ua)
	if len(match) < 2 {
		return major, full
	}
	full = strings.TrimSpace(match[1])
	parts := strings.Split(full, ".")
	if len(parts) > 0 && strings.TrimSpace(parts[0]) != "" {
		major = strings.TrimSpace(parts[0])
	}
	if strings.Count(full, ".") == 0 {
		full = full + ".0.0.0"
	}
	return major, full
}

func visibleGlyphCount(text string) int {
	count := 0
	for _, r := range text {
		if unicode.Is(unicode.Mn, r) || unicode.Is(unicode.Mc, r) || unicode.Is(unicode.Me, r) {
			continue
		}
		if r == '\u200d' || r == '\u200c' || r == '\ufe0f' {
			continue
		}
		count++
	}
	if count <= 0 {
		count = utf8.RuneCountInString(text)
	}
	if count <= 0 {
		count = 1
	}
	return count
}

func (s *turnstileSolver) solve(requirementsToken, dx string) (string, error) {
	s.regs = map[string]any{}
	s.window = s.buildWindow()
	s.done = false
	s.resolved = ""
	s.rejected = ""
	s.stepCount = 0
	s.objectSeq = 0
	s.initRuntime()

	s.setReg(turnstileSuccessReg, vmFunc(func(args ...any) (any, error) {
		if !s.done {
			s.done = true
			var value any
			if len(args) > 0 {
				value = args[0]
			}
			s.resolved = latin1Base64Encode(s.jsToString(value))
		}
		return nil, nil
	}))
	s.setReg(turnstileErrorReg, vmFunc(func(args ...any) (any, error) {
		if !s.done {
			s.done = true
			var value any
			if len(args) > 0 {
				value = args[0]
			}
			s.rejected = latin1Base64Encode(s.jsToString(value))
		}
		return nil, nil
	}))
	s.setReg(turnstileCallbackReg, vmFunc(func(args ...any) (any, error) {
		if len(args) < 3 {
			return nil, nil
		}
		targetReg := args[0]
		returnReg := args[1]
		argRegs, _ := args[2].([]any)
		innerQueue := argRegs
		mappedArgRegs := []any{}
		if len(args) >= 4 {
			if mapped, ok := args[2].([]any); ok {
				mappedArgRegs = mapped
			}
			if queueValue, ok := args[3].([]any); ok {
				innerQueue = queueValue
			}
		}
		s.setReg(targetReg, vmFunc(func(callArgs ...any) (any, error) {
			if s.done {
				return nil, nil
			}
			previousQueue := s.copyQueue()
			for i, regID := range mappedArgRegs {
				if i < len(callArgs) {
					s.setReg(regID, callArgs[i])
				} else {
					s.setReg(regID, nil)
				}
			}
			s.setReg(turnstileQueueReg, copyAnySlice(innerQueue))
			err := s.runQueue()
			s.setReg(turnstileQueueReg, previousQueue)
			if err != nil {
				return err.Error(), nil
			}
			return s.getReg(returnReg), nil
		}))
		return nil, nil
	}))
	s.setReg(turnstileKeyReg, requirementsToken)

	decoded, err := latin1Base64Decode(dx)
	if err != nil {
		return "", err
	}
	plain := xorString(decoded, requirementsToken)
	var queue []any
	if err := json.Unmarshal([]byte(plain), &queue); err != nil {
		return "", err
	}
	s.setReg(turnstileQueueReg, queue)
	if err := s.runQueue(); err != nil && !s.done {
		if success, ok := s.getReg(turnstileSuccessReg).(vmFunc); ok {
			_, _ = success(fmt.Sprintf("%d: %v", s.stepCount, err))
		}
	}
	if s.rejected != "" {
		return "", errors.New(s.rejected)
	}
	if s.resolved == "" {
		return "", fmt.Errorf("turnstile vm unresolved after %d steps", s.stepCount)
	}
	s.dumpTrace()
	return s.resolved, nil
}

func (s *turnstileSolver) initRuntime() {
	s.setReg(0, vmFunc(func(args ...any) (any, error) {
		if len(args) == 0 {
			return nil, nil
		}
		value, err := solveTurnstileDXWithSession(s.jsToString(s.getReg(turnstileKeyReg)), s.jsToString(args[0]), s.session)
		if err != nil {
			return nil, err
		}
		return value, nil
	}))
	s.setReg(1, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		target, keyReg := args[0], args[1]
		s.setReg(target, xorString(s.jsToString(s.getReg(target)), s.jsToString(s.getReg(keyReg))))
		return nil, nil
	}))
	s.setReg(2, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		s.setReg(args[0], args[1])
		return nil, nil
	}))
	s.setReg(5, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		left := s.getReg(args[0])
		right := s.getReg(args[1])
		if arr, ok := left.([]any); ok {
			s.setReg(args[0], append(arr, right))
			return nil, nil
		}
		if lNum, ok := s.asNumber(left); ok {
			if rNum, ok := s.asNumber(right); ok {
				s.setReg(args[0], lNum+rNum)
				return nil, nil
			}
		}
		s.setReg(args[0], s.jsToString(left)+s.jsToString(right))
		return nil, nil
	}))
	s.setReg(6, vmFunc(func(args ...any) (any, error) {
		if len(args) < 3 {
			return nil, nil
		}
		s.setReg(args[0], s.jsGetProp(s.getReg(args[1]), s.getReg(args[2])))
		return nil, nil
	}))
	s.setReg(7, vmFunc(func(args ...any) (any, error) {
		if len(args) == 0 {
			return nil, nil
		}
		_, err := s.callFn(s.getReg(args[0]), s.derefArgs(args[1:])...)
		return nil, err
	}))
	s.setReg(8, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		s.setReg(args[0], s.getReg(args[1]))
		return nil, nil
	}))
	s.setReg(11, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		pattern := s.jsToString(s.getReg(args[1]))
		rx, err := regexp.Compile(pattern)
		if err != nil {
			s.setReg(args[0], nil)
			return nil, nil
		}
		scripts, _ := s.jsGetProp(s.jsGetProp(s.window, "document"), "scripts").([]any)
		for _, item := range scripts {
			src := s.jsToString(s.jsGetProp(item, "src"))
			if src == "" {
				continue
			}
			if hit := rx.FindString(src); hit != "" {
				s.setReg(args[0], hit)
				return nil, nil
			}
		}
		s.setReg(args[0], nil)
		return nil, nil
	}))
	s.setReg(12, vmFunc(func(args ...any) (any, error) {
		if len(args) == 0 {
			return nil, nil
		}
		s.setReg(args[0], regMapRef{solver: s})
		return nil, nil
	}))
	s.setReg(13, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		_, err := s.callFn(s.getReg(args[1]), args[2:]...)
		if err != nil {
			s.setReg(args[0], err.Error())
		}
		return nil, nil
	}))
	s.setReg(14, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		var out any
		if err := json.Unmarshal([]byte(s.jsToString(s.getReg(args[1]))), &out); err != nil {
			return nil, err
		}
		s.setReg(args[0], out)
		return nil, nil
	}))
	s.setReg(15, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		body, err := jsJSONStringify(s.getReg(args[1]))
		if err != nil {
			return nil, err
		}
		s.setReg(args[0], body)
		return nil, nil
	}))
	s.setReg(17, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		value, err := s.callFn(s.getReg(args[1]), s.derefArgs(args[2:])...)
		if err != nil {
			s.setReg(args[0], err.Error())
			return nil, nil
		}
		s.setReg(args[0], value)
		return nil, nil
	}))
	s.setReg(18, vmFunc(func(args ...any) (any, error) {
		if len(args) == 0 {
			return nil, nil
		}
		decoded, err := latin1Base64Decode(s.jsToString(s.getReg(args[0])))
		if err != nil {
			return nil, err
		}
		s.setReg(args[0], decoded)
		return nil, nil
	}))
	s.setReg(19, vmFunc(func(args ...any) (any, error) {
		if len(args) == 0 {
			return nil, nil
		}
		s.setReg(args[0], latin1Base64Encode(s.jsToString(s.getReg(args[0]))))
		return nil, nil
	}))
	s.setReg(20, vmFunc(func(args ...any) (any, error) {
		if len(args) < 3 {
			return nil, nil
		}
		if s.valuesEqual(s.getReg(args[0]), s.getReg(args[1])) {
			_, err := s.callFn(s.getReg(args[2]), args[3:]...)
			return nil, err
		}
		return nil, nil
	}))
	s.setReg(21, vmFunc(func(args ...any) (any, error) {
		if len(args) < 4 {
			return nil, nil
		}
		left, _ := s.asNumber(s.getReg(args[0]))
		right, _ := s.asNumber(s.getReg(args[1]))
		threshold, _ := s.asNumber(s.getReg(args[2]))
		if math.Abs(left-right) > threshold {
			_, err := s.callFn(s.getReg(args[3]), args[4:]...)
			return nil, err
		}
		return nil, nil
	}))
	s.setReg(22, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		previousQueue := s.copyQueue()
		if nextQueue, ok := args[1].([]any); ok {
			s.setReg(turnstileQueueReg, copyAnySlice(nextQueue))
		} else {
			s.setReg(turnstileQueueReg, []any{})
		}
		err := s.runQueue()
		s.setReg(turnstileQueueReg, previousQueue)
		if err != nil {
			s.setReg(args[0], err.Error())
		}
		return nil, nil
	}))
	s.setReg(23, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		if s.getReg(args[0]) == nil {
			return nil, nil
		}
		_, err := s.callFn(s.getReg(args[1]), args[2:]...)
		return nil, err
	}))
	s.setReg(24, vmFunc(func(args ...any) (any, error) {
		if len(args) < 3 {
			return nil, nil
		}
		method := s.jsGetProp(s.getReg(args[1]), s.getReg(args[2]))
		if isCallableJSValue(method) {
			s.setReg(args[0], method)
		} else {
			s.setReg(args[0], nil)
		}
		return nil, nil
	}))
	s.setReg(25, vmFunc(func(args ...any) (any, error) { return nil, nil }))
	s.setReg(26, vmFunc(func(args ...any) (any, error) { return nil, nil }))
	s.setReg(27, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		left := s.getReg(args[0])
		right := s.getReg(args[1])
		if arr, ok := left.([]any); ok {
			filtered := arr[:0]
			for _, item := range arr {
				if !s.valuesEqual(item, right) {
					filtered = append(filtered, item)
				}
			}
			s.setReg(args[0], append([]any{}, filtered...))
			return nil, nil
		}
		lNum, lok := s.asNumber(left)
		rNum, rok := s.asNumber(right)
		if lok && rok {
			s.setReg(args[0], lNum-rNum)
		}
		return nil, nil
	}))
	s.setReg(28, vmFunc(func(args ...any) (any, error) { return nil, nil }))
	s.setReg(29, vmFunc(func(args ...any) (any, error) {
		if len(args) < 3 {
			return nil, nil
		}
		left, _ := s.asNumber(s.getReg(args[1]))
		right, _ := s.asNumber(s.getReg(args[2]))
		s.setReg(args[0], left < right)
		return nil, nil
	}))
	s.setReg(33, vmFunc(func(args ...any) (any, error) {
		if len(args) < 3 {
			return nil, nil
		}
		left, _ := s.asNumber(s.getReg(args[1]))
		right, _ := s.asNumber(s.getReg(args[2]))
		s.setReg(args[0], left*right)
		return nil, nil
	}))
	s.setReg(34, vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		s.setReg(args[0], s.getReg(args[1]))
		return nil, nil
	}))
	s.setReg(35, vmFunc(func(args ...any) (any, error) {
		if len(args) < 3 {
			return nil, nil
		}
		left, _ := s.asNumber(s.getReg(args[1]))
		right, _ := s.asNumber(s.getReg(args[2]))
		if right == 0 {
			s.setReg(args[0], float64(0))
		} else {
			s.setReg(args[0], left/right)
		}
		return nil, nil
	}))
	s.setReg(turnstileWindowReg, s.window)
}

func (s *turnstileSolver) buildWindow() map[string]any {
	ua := "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
	lang := "en"
	languagesJoin := "en"
	width := 2560
	height := 1440
	innerWidth := 800
	innerHeight := 600
	outerWidth := 160
	outerHeight := 28
	screenX := 0
	screenY := 0
	hardwareConcurrency := 12
	heapLimit := int64(4294967296)
	deviceID := "bb13486d-db99-4547-81a4-a8f2a6351be9"
	timeOrigin := float64(time.Now().Add(-10 * time.Second).UnixMilli())
	performanceNow := 9270.399999976158
	mathRandomSequence := []float64{0.15625, 0.28125, 0.421875, 0.125}
	vendor := "Google Inc."
	platform := "Win32"
	documentProbe := "_reactListeningx9ytk7ovr7"
	windowProbe := "onmouseover"
	scriptURL := "https://sentinel.openai.com/backend-api/sentinel/sdk.js"
	productSub := "20030107"
	if s.profile.ScreenSum > 0 {
		width, height = splitScreenSum(s.profile.ScreenSum)
	}
	if s.profile.HeapLimit > 0 {
		heapLimit = s.profile.HeapLimit
	}
	if strings.TrimSpace(s.profile.UserAgent) != "" {
		ua = s.profile.UserAgent
	}
	if strings.TrimSpace(s.profile.ScriptURL) != "" {
		scriptURL = strings.TrimSpace(s.profile.ScriptURL)
	}
	if strings.TrimSpace(s.profile.Language) != "" {
		lang = s.profile.Language
	}
	if strings.TrimSpace(s.profile.LanguagesJoin) != "" {
		languagesJoin = s.profile.LanguagesJoin
	}
	if s.profile.HardwareConcurrency > 0 {
		hardwareConcurrency = s.profile.HardwareConcurrency
	}
	if s.profile.TimeOrigin > 0 {
		timeOrigin = s.profile.TimeOrigin
	}
	if s.profile.PerformanceNow > 0 {
		performanceNow = s.profile.PerformanceNow
	}
	if strings.TrimSpace(s.profile.DocumentProbe) != "" {
		documentProbe = strings.TrimSpace(s.profile.DocumentProbe)
	}
	if strings.TrimSpace(s.profile.WindowProbe) != "" {
		windowProbe = strings.TrimSpace(s.profile.WindowProbe)
	}
	if strings.Contains(s.profile.NavigatorProbe, "productSub") {
		parts := strings.SplitN(s.profile.NavigatorProbe, "−", 2)
		if len(parts) == 2 && strings.TrimSpace(parts[1]) != "" {
			productSub = strings.TrimSpace(parts[1])
		}
	}
	if s.session != nil {
		if strings.TrimSpace(s.session.UserAgent) != "" {
			ua = s.session.UserAgent
		}
		if strings.TrimSpace(s.session.Language) != "" {
			lang = s.session.Language
		}
		if strings.TrimSpace(s.session.LanguagesJoin) != "" {
			languagesJoin = s.session.LanguagesJoin
		}
		if s.session.ScreenWidth > 0 {
			width = s.session.ScreenWidth
		}
		if s.session.ScreenHeight > 0 {
			height = s.session.ScreenHeight
		}
		if s.session.HardwareConcurrency > 0 {
			hardwareConcurrency = s.session.HardwareConcurrency
		}
		if s.session.HeapLimit > 0 {
			heapLimit = s.session.HeapLimit
		}
		if strings.TrimSpace(s.session.DeviceID) != "" {
			deviceID = s.session.DeviceID
		}
		if s.session.Persona.TimeOrigin > 0 {
			timeOrigin = s.session.Persona.TimeOrigin
		}
		if strings.TrimSpace(s.session.Persona.Vendor) != "" {
			vendor = s.session.Persona.Vendor
		}
		if strings.TrimSpace(s.session.Persona.Platform) != "" {
			platform = s.session.Persona.Platform
		}
		if s.session.Persona.PerformanceNow > 0 && s.profile.PerformanceNow <= 0 {
			performanceNow = s.session.Persona.PerformanceNow
		}
		if len(s.session.Persona.MathRandomSequence) > 0 {
			mathRandomSequence = append([]float64{}, s.session.Persona.MathRandomSequence...)
		}
		if strings.TrimSpace(s.session.Persona.DocumentProbe) != "" && strings.TrimSpace(s.profile.DocumentProbe) == "" {
			documentProbe = strings.TrimSpace(s.session.Persona.DocumentProbe)
		}
		if strings.TrimSpace(s.session.Persona.WindowProbe) != "" && strings.TrimSpace(s.profile.WindowProbe) == "" {
			windowProbe = strings.TrimSpace(s.session.Persona.WindowProbe)
		}
	}
	chromeMajor, chromeFullVersion := chromeVersionHints(ua)
	location := namedObject("location", map[string]any{
		"ancestorOrigins": withOrderedKeys(map[string]any{}, []string{}),
		"href":            "https://auth.openai.com/create-account/password",
		"origin":          "https://auth.openai.com",
		"protocol":        "https:",
		"host":            "auth.openai.com",
		"hostname":        "auth.openai.com",
		"port":            "",
		"pathname":        "/create-account/password",
		"search":          "",
		"hash":            "",
		"assign":          nativeVMFuncNamed("assign", vmFunc(func(args ...any) (any, error) { return nil, nil })),
		"reload":          nativeVMFuncNamed("reload", vmFunc(func(args ...any) (any, error) { return nil, nil })),
		"replace":         nativeVMFuncNamed("replace", vmFunc(func(args ...any) (any, error) { return nil, nil })),
		"toString":        nativeVMFuncNamed("toString", vmFunc(func(args ...any) (any, error) { return "https://auth.openai.com/create-account/password", nil })),
	}, []string{
		"ancestorOrigins",
		"href",
		"origin",
		"protocol",
		"host",
		"hostname",
		"port",
		"pathname",
		"search",
		"hash",
		"assign",
		"reload",
		"replace",
		"toString",
	})
	location = withOwnPropertyNames(location, []string{
		"valueOf",
		"ancestorOrigins",
		"href",
		"origin",
		"protocol",
		"host",
		"hostname",
		"port",
		"pathname",
		"search",
		"hash",
		"assign",
		"reload",
		"replace",
		"toString",
	})
	location["valueOf"] = nativeVMFuncNamed("valueOf", vmFunc(func(args ...any) (any, error) { return location, nil }))
	scriptCandidates := []string{
		scriptURL,
		"https://sentinel.openai.com/backend-api/sentinel/sdk.js",
		"https://sentinel.openai.com/sentinel/20260219f9f6/sdk.js",
	}
	scripts := make([]any, 0, len(scriptCandidates))
	seenScripts := map[string]struct{}{}
	for _, candidate := range scriptCandidates {
		candidate = strings.TrimSpace(candidate)
		if candidate == "" {
			continue
		}
		if _, exists := seenScripts[candidate]; exists {
			continue
		}
		seenScripts[candidate] = struct{}{}
		scripts = append(scripts, withOrderedKeys(map[string]any{"src": candidate}, []string{}))
	}
	sessionTimestamp := int64(timeOrigin)
	if sessionTimestamp <= 0 {
		sessionTimestamp = time.Now().UnixMilli()
	}
	statsigEvalKey := "statsig.cached.evaluations.1360634483"
	storageEnumerableKeys := []string{
		"setItem",
	}
	localStorageData := withOrderedKeys(map[string]any{
		statsigEvalKey: `{"source":"Network","data":"{}","receivedAt":0,"stableID":"` + deviceID + `"}`,
		"statsig.session_id.444584300": fmt.Sprintf(
			`{"sessionID":"%s","startTime":%d,"lastUpdate":%d}`,
			deviceID,
			sessionTimestamp,
			sessionTimestamp,
		),
		"statsig.last_modified_time.evaluations": fmt.Sprintf(
			`{"%s":%d}`,
			statsigEvalKey,
			sessionTimestamp+351,
		),
		"statsig.stable_id.444584300": `"` + deviceID + `"`,
	}, []string{
		statsigEvalKey,
		"statsig.session_id.444584300",
		"statsig.last_modified_time.evaluations",
		"statsig.stable_id.444584300",
	})
	storageKeys := []string{
		statsigEvalKey,
		"statsig.session_id.444584300",
		"statsig.last_modified_time.evaluations",
		"statsig.stable_id.444584300",
	}
	storageProto := withOwnPropertyNames(namedObject("Storage.prototype", map[string]any{
		"length": nil,
	}, []string{"length", "clear", "getItem", "key", "removeItem", "setItem", "constructor"}), []string{"length", "clear", "getItem", "key", "removeItem", "setItem", "constructor"})
	localStorage := withOwnPropertyNames(namedObject("localStorage", map[string]any{
		"__storage_data__":            localStorageData,
		"__storage_keys__":            append([]string{}, storageKeys...),
		"__storage_enumerable_keys__": mergeOrderedKeys(storageEnumerableKeys, storageKeys),
		"length":                      float64(len(storageKeys)),
	}, []string{}), mergeOrderedKeys(storageEnumerableKeys, storageKeys))
	localStorage[prototypeMeta] = storageProto
	refreshLocalStorageMeta := func() {
		ordered := make([]string, 0, len(storageKeys))
		seen := map[string]struct{}{}
		for _, key := range storageKeys {
			if _, exists := localStorageData[key]; !exists {
				continue
			}
			if _, exists := seen[key]; exists {
				continue
			}
			ordered = append(ordered, key)
			seen[key] = struct{}{}
		}
		for _, key := range keysOfMap(localStorageData) {
			if _, exists := seen[key]; exists {
				continue
			}
			ordered = append(ordered, key)
			seen[key] = struct{}{}
		}
		storageKeys = ordered
		enumerable := append([]string{}, storageEnumerableKeys...)
		for _, key := range storageKeys {
			if _, exists := localStorageData[key]; !exists {
				continue
			}
			enumerable = append(enumerable, key)
		}
		localStorage["__storage_keys__"] = append([]string{}, storageKeys...)
		localStorage["__storage_enumerable_keys__"] = enumerable
		localStorage["length"] = float64(len(storageKeys))
		localStorage[ownPropertyNamesMeta] = mergeOrderedKeys(storageEnumerableKeys, storageKeys)
	}
	storageProto["length"] = nil
	storageProto["key"] = nativeVMFuncNamed("key", vmFunc(func(args ...any) (any, error) {
		if len(args) == 0 {
			return nil, nil
		}
		idx := toIntIndex(args[0])
		if idx < 0 || idx >= len(storageKeys) {
			return nil, nil
		}
		return storageKeys[idx], nil
	}))
	storageProto["getItem"] = nativeVMFuncNamed("getItem", vmFunc(func(args ...any) (any, error) {
		if len(args) == 0 {
			return nil, nil
		}
		return localStorageData[s.jsToString(args[0])], nil
	}))
	storageProto["setItem"] = nativeVMFuncNamed("setItem", vmFunc(func(args ...any) (any, error) {
		if len(args) < 2 {
			return nil, nil
		}
		key := s.jsToString(args[0])
		s.traceDetail("localStorage.setItem.key", key)
		_, existed := localStorageData[key]
		localStorageData[key] = s.jsToString(args[1])
		if !existed {
			insertAt := len(storageKeys)
			if insertAt >= 2 {
				insertAt -= 2
			}
			if insertAt < 0 {
				insertAt = 0
			}
			storageKeys = append(storageKeys, "")
			copy(storageKeys[insertAt+1:], storageKeys[insertAt:])
			storageKeys[insertAt] = key
		}
		refreshLocalStorageMeta()
		return nil, nil
	}))
	storageProto["removeItem"] = nativeVMFuncNamed("removeItem", vmFunc(func(args ...any) (any, error) {
		if len(args) == 0 {
			return nil, nil
		}
		delete(localStorageData, s.jsToString(args[0]))
		refreshLocalStorageMeta()
		return nil, nil
	}))
	storageProto["clear"] = nativeVMFuncNamed("clear", vmFunc(func(args ...any) (any, error) {
		for key := range localStorageData {
			delete(localStorageData, key)
		}
		refreshLocalStorageMeta()
		return nil, nil
	}))
	screen := namedObject("screen", map[string]any{
		"availWidth":  float64(width),
		"availHeight": float64(1392),
		"availLeft":   float64(0),
		"availTop":    float64(0),
		"colorDepth":  float64(32),
		"pixelDepth":  float64(32),
		"width":       float64(width),
		"height":      float64(height),
	}, []string{})
	reactContainerKey := "__reactContainer$9jwm15aojmb"
	secondaryDocumentProbe := "_reactListeningki2lzegitkk"
	document := namedObject("document", map[string]any{
		"scripts":  scripts,
		"location": location,
		"documentElement": withOrderedKeys(map[string]any{
			"getAttribute": vmFunc(func(args ...any) (any, error) {
				return nil, nil
			}),
		}, []string{}),
		reactContainerKey:      withOrderedKeys(map[string]any{}, []string{}),
		secondaryDocumentProbe: true,
	}, []string{
		documentProbe,
		"location",
		"createElement",
		reactContainerKey,
		secondaryDocumentProbe,
	})
	domRectProto := withOwnPropertyNames(namedObject("DOMRect.prototype", map[string]any{
		"x":      float64(8),
		"y":      float64(8),
		"width":  float64(484),
		"height": float64(167.5416717529297),
		"top":    float64(8),
		"left":   float64(8),
		"right":  float64(492),
		"bottom": float64(175.5416717529297),
		"toJSON": nativeVMFuncNamed("toJSON", vmFunc(func(args ...any) (any, error) {
			return withOrderedKeys(map[string]any{
				"x":      float64(8),
				"y":      float64(8),
				"width":  float64(484),
				"height": float64(167.5416717529297),
				"top":    float64(8),
				"left":   float64(8),
				"right":  float64(492),
				"bottom": float64(175.5416717529297),
			}, []string{"x", "y", "width", "height", "top", "left", "right", "bottom"}), nil
		})),
		"constructor": nil,
	}, []string{"x", "y", "width", "height", "constructor"}), []string{"x", "y", "width", "height", "constructor"})
	bodyProbeSuffix := "qjnhk43as9q"
	if strings.TrimSpace(documentProbe) != "" {
		bodyProbeSuffix = strings.TrimPrefix(strings.TrimSpace(documentProbe), "_reactListening")
		if bodyProbeSuffix == "" {
			bodyProbeSuffix = "qjnhk43as9q"
		}
	}
	bodyProto := withOwnPropertyNames(namedObject("HTMLBodyElement.prototype", map[string]any{
		"getBoundingClientRect": nativeVMFuncNamed("getBoundingClientRect", vmFunc(func(args ...any) (any, error) {
			rect := withOwnPropertyNames(namedObject("DOMRect", map[string]any{}, []string{}), []string{})
			rect[prototypeMeta] = domRectProto
			return rect, nil
		})),
		"appendChild": nativeVMFuncNamed("appendChild", vmFunc(func(args ...any) (any, error) {
			s.traceCall("HTMLBodyElement.prototype.appendChild.call")
			if len(args) > 0 {
				return args[0], nil
			}
			return nil, nil
		})),
		"removeChild": nativeVMFuncNamed("removeChild", vmFunc(func(args ...any) (any, error) {
			s.traceCall("HTMLBodyElement.prototype.removeChild.call")
			if len(args) > 0 {
				return args[0], nil
			}
			return nil, nil
		})),
	}, []string{"getBoundingClientRect", "appendChild", "removeChild"}), []string{"getBoundingClientRect", "appendChild", "removeChild"})
	document["body"] = withOwnPropertyNames(withOrderedKeys(map[string]any{
		"__reactFiber$" + bodyProbeSuffix: withOrderedKeys(map[string]any{}, []string{}),
		"__reactProps$" + bodyProbeSuffix: withOrderedKeys(map[string]any{}, []string{}),
	}, []string{
		"__reactFiber$" + bodyProbeSuffix,
		"__reactProps$" + bodyProbeSuffix,
	}), []string{
		"__reactFiber$" + bodyProbeSuffix,
		"__reactProps$" + bodyProbeSuffix,
	})
	document["body"].(map[string]any)[prototypeMeta] = bodyProto
	document["getElementById"] = vmFunc(func(args ...any) (any, error) {
		return document["body"], nil
	})
	document["querySelector"] = vmFunc(func(args ...any) (any, error) {
		return document["body"], nil
	})
	document["createElement"] = stringifiedVMFuncSource(`function createElement(tagName) {
      const element = originalCreateElement.apply(this, arguments);
      if (String(tagName || '').toLowerCase() !== 'iframe') {
        return element;
      }

      const originalSrcdoc = element.srcdoc;
      Object.defineProperty(element, 'srcdoc', {
        configurable: true,
        get: () => originalSrcdoc,
        set(value) {
          addContentWindowProxy(this);
          Object.defineProperty(this, 'srcdoc', {
            configurable: false,
            writable: false,
            value: originalSrcdoc,
          });
          this.setAttribute('srcdoc', value);
        },
      });
      return element;
	}`, vmFunc(func(args ...any) (any, error) {
		tag := ""
		if len(args) > 0 {
			tag = strings.ToLower(s.jsToString(args[0]))
		}
		s.traceCall("document.createElement(" + tag + ")")
		elementName := "HTMLElement"
		switch tag {
		case "div":
			elementName = "HTMLDivElement"
		case "iframe":
			elementName = "HTMLIFrameElement"
		case "canvas":
			elementName = "HTMLCanvasElement"
		case "span":
			elementName = "HTMLSpanElement"
		}
		styleObj := namedObject("CSSStyleDeclaration", map[string]any{}, []string{})
		var element map[string]any
		measureElementRect := nativeVMFuncNamed("getBoundingClientRect", vmFunc(func(args ...any) (any, error) {
			fontSize := 20.0
			rawFontSize := strings.TrimSpace(s.jsToString(styleObj["fontSize"]))
			rawFontSize = strings.TrimSuffix(rawFontSize, "px")
			if parsed, err := strconv.ParseFloat(strings.TrimSpace(rawFontSize), 64); err == nil && parsed > 0 {
				fontSize = parsed
			}
			fontFamily := strings.TrimSpace(s.jsToString(styleObj["fontFamily"]))
			if fontFamily == "" {
				fontFamily = "Times New Roman"
			}
			textValue := s.jsToString(element["innerText"])
			glyphCount := visibleGlyphCount(textValue)
			widthFactor := 0.56
			height := 22.666667938232422
			if strings.EqualFold(fontFamily, "Times New Roman") {
				widthFactor = 0.4810416667
				height = 22.666667938232422
			} else if strings.EqualFold(fontFamily, "Impact") {
				widthFactor = 0.5927855174
				height = 22.666667938232422
			} else if strings.EqualFold(fontFamily, "Garamond") {
				widthFactor = 0.5475
				height = 22.666667938232422
			} else if strings.EqualFold(fontFamily, "Arial") {
				widthFactor = 0.5565
				height = 22.666667938232422
			} else if strings.EqualFold(fontFamily, "Verdana") {
				widthFactor = 0.568
				height = 22.666667938232422
			}
			widthValue := math.Max(fontSize, float64(glyphCount)*fontSize*widthFactor)
			if glyphCount == 3 {
				widthValue = 22.666667938232422
				height = 20.0
			}
			topValue := 175.5416717529297
			leftValue := 8.0
			rect := withOwnPropertyNames(namedObject("DOMRect", map[string]any{}, []string{}), []string{})
			rectProto := withOwnPropertyNames(namedObject("DOMRect.prototype", map[string]any{
				"x":      leftValue,
				"y":      topValue,
				"width":  widthValue,
				"height": height,
				"top":    topValue,
				"left":   leftValue,
				"right":  leftValue + widthValue,
				"bottom": topValue + height,
				"toJSON": nativeVMFuncNamed("toJSON", vmFunc(func(args ...any) (any, error) {
					return withOrderedKeys(map[string]any{
						"x":      leftValue,
						"y":      topValue,
						"width":  widthValue,
						"height": height,
						"top":    topValue,
						"left":   leftValue,
						"right":  leftValue + widthValue,
						"bottom": topValue + height,
					}, []string{"x", "y", "width", "height", "top", "left", "right", "bottom"}), nil
				})),
				"constructor": nil,
			}, []string{"x", "y", "width", "height", "constructor"}), []string{"x", "y", "width", "height", "constructor"})
			rect[prototypeMeta] = rectProto
			s.traceDetail("HTMLDivElement.getBoundingClientRect.rect", withOrderedKeys(map[string]any{
				"x":      leftValue,
				"y":      topValue,
				"width":  widthValue,
				"height": height,
				"top":    topValue,
				"left":   leftValue,
				"right":  leftValue + widthValue,
				"bottom": topValue + height,
			}, []string{"x", "y", "width", "height", "top", "left", "right", "bottom"}))
			return rect, nil
		}))
		element = withOwnPropertyNames(withOrderedKeys(map[string]any{
			"tagName": strings.ToUpper(tag),
			"style":   styleObj,
			"appendChild": nativeVMFuncNamed("appendChild", vmFunc(func(args ...any) (any, error) {
				if len(args) > 0 {
					return args[0], nil
				}
				return nil, nil
			})),
			"removeChild": nativeVMFuncNamed("removeChild", vmFunc(func(args ...any) (any, error) {
				if len(args) > 0 {
					return args[0], nil
				}
				return nil, nil
			})),
			"remove": nativeVMFuncNamed("remove", vmFunc(func(args ...any) (any, error) { return nil, nil })),
			"getBoundingClientRect": measureElementRect,
		}, []string{}), []string{})
		element[objectNameMeta] = elementName
		if tag == "canvas" {
			element["getContext"] = vmFunc(func(args ...any) (any, error) {
				return withOrderedKeys(map[string]any{
					"getExtension": vmFunc(func(args ...any) (any, error) {
						if len(args) > 0 && s.jsToString(args[0]) == "WEBGL_debug_renderer_info" {
							return withOrderedKeys(map[string]any{
								"UNMASKED_VENDOR_WEBGL":   float64(37445),
								"UNMASKED_RENDERER_WEBGL": float64(37446),
							}, []string{}), nil
						}
						return nil, nil
					}),
					"getParameter": vmFunc(func(args ...any) (any, error) {
						if len(args) == 0 {
							return nil, nil
						}
						param := toIntIndex(args[0])
						switch param {
						case 37445, 7936:
							return "Google Inc. (NVIDIA)", nil
						case 37446, 7937:
							return "ANGLE (NVIDIA, NVIDIA GeForce RTX 5080 Laptop GPU Direct3D11 vs_5_0 ps_5_0, D3D11)", nil
						default:
							return nil, nil
						}
					}),
				}, []string{}), nil
			})
		}
		if tag == "iframe" {
			element["srcdoc"] = ""
			element[ownPropertyNamesMeta] = []string{"srcdoc"}
		}
		return element, nil
	}))
	navigator := namedObject("navigator", map[string]any{
		"vendorSub":           "",
		"productSub":          productSub,
		"userAgent":           ua,
		"vendor":              vendor,
		"platform":            platform,
		"hardwareConcurrency": float64(hardwareConcurrency),
		"deviceMemory":        float64(32),
		"maxTouchPoints":      float64(0),
		"appCodeName":         "Mozilla",
		"appName":             "Netscape",
		"appVersion":          strings.TrimPrefix(ua, "Mozilla/5.0 "),
		"language":            lang,
		"languages":           stringSliceToAny(strings.Split(strings.ReplaceAll(languagesJoin, ";q=0.9", ""), ",")),
		"webdriver":           false,
	}, authNavigatorKeyOrder)
	navigator["clipboard"] = namedObject("navigator.clipboard", map[string]any{}, []string{})
	navigator["xr"] = namedObject("navigator.xr", map[string]any{}, []string{})
	navigator["storage"] = namedObject("navigator.storage", map[string]any{
		"estimate": vmFunc(func(args ...any) (any, error) {
			return withOrderedKeys(map[string]any{
				"quota":        float64(306461727129),
				"usage":        float64(0),
				"usageDetails": withOrderedKeys(map[string]any{}, []string{}),
			}, []string{}), nil
		}),
	}, []string{})
	navigator["userAgentData"] = namedObject("navigator.userAgentData", map[string]any{
		"brands": []any{
			withOrderedKeys(map[string]any{"brand": "Google Chrome", "version": chromeMajor}, []string{}),
			withOrderedKeys(map[string]any{"brand": "Not.A/Brand", "version": "8"}, []string{}),
			withOrderedKeys(map[string]any{"brand": "Chromium", "version": chromeMajor}, []string{}),
		},
		"mobile":   false,
		"platform": "Windows",
		"getHighEntropyValues": stringifiedVMFuncSource(`async (hints) => {
        const data = {
          architecture: config.architecture,
          bitness: config.bitness,
          brands: config.brands,
          fullVersionList: config.fullVersionList,
          mobile: !!config.mobile,
          model: config.model,
          platform: config.platformName,
          platformVersion: config.platformVersion,
          uaFullVersion: config.fullVersion,
          wow64: !!config.wow64,
        };
        const requested = {};
        for (const hint of hints || []) {
          if (hint in data) {
            requested[hint] = data[hint];
          }
        }
        requested.brands = data.brands;
        requested.mobile = data.mobile;
        requested.platform = data.platform;
        return requested;
      }`, vmFunc(func(args ...any) (any, error) {
			return withOrderedKeys(map[string]any{
				"platform":        "Windows",
				"platformVersion": "19.0.0",
				"architecture":    "x86",
				"bitness":         "64",
				"model":           "",
				"uaFullVersion":   chromeFullVersion,
				"fullVersionList": []any{
					withOrderedKeys(map[string]any{"brand": "Google Chrome", "version": chromeFullVersion}, []string{}),
					withOrderedKeys(map[string]any{"brand": "Not.A/Brand", "version": "8.0.0.0"}, []string{}),
					withOrderedKeys(map[string]any{"brand": "Chromium", "version": chromeFullVersion}, []string{}),
				},
				"wow64": false,
			}, []string{}), nil
		})),
		"toJSON": stringifiedVMFuncSource(`toJSON() {
        return { brands: this.brands, mobile: this.mobile, platform: this.platform };
      }`, vmFunc(func(args ...any) (any, error) {
			return withOrderedKeys(map[string]any{
				"brands": []any{
					withOrderedKeys(map[string]any{"brand": "Google Chrome", "version": chromeMajor}, []string{}),
					withOrderedKeys(map[string]any{"brand": "Not.A/Brand", "version": "8"}, []string{}),
					withOrderedKeys(map[string]any{"brand": "Chromium", "version": chromeMajor}, []string{}),
				},
				"mobile":   false,
				"platform": "Windows",
			}, []string{"brands", "mobile", "platform"}), nil
		})),
	}, []string{})
	navigator["canLoadAdAuctionFencedFrame"] = nativeVMFuncNamed("canLoadAdAuctionFencedFrame", vmFunc(func(args ...any) (any, error) {
		return nil, nil
	}))
	start := time.Now()
	navigator["userAgentData"] = withOwnPropertyNames(
		navigator["userAgentData"].(map[string]any),
		[]string{"brands", "mobile", "platform", "getHighEntropyValues", "toJSON"},
	)
	navigator[prototypeMeta] = withOwnPropertyNames(
		withOrderedKeys(map[string]any{}, authNavigatorPrototypeKeys),
		authNavigatorPrototypeOwnPropertyNames,
	)
	for _, key := range authNavigatorPrototypeKeys {
		if _, exists := navigator[prototypeMeta].(map[string]any)[key]; !exists {
			navigator[prototypeMeta].(map[string]any)[key] = nil
		}
	}
	for _, key := range authNavigatorPrototypeOwnPropertyNames {
		if _, exists := navigator[prototypeMeta].(map[string]any)[key]; !exists {
			navigator[prototypeMeta].(map[string]any)[key] = nil
		}
	}
	window := withOwnPropertyNames(namedObject("window", map[string]any{}, authWindowKeyOrder), mergeOrderedKeys(authWindowOwnPropertyNames, authWindowKeyOrder))
	window["Reflect"] = namedObject("Reflect", map[string]any{
		"defineProperty": nativeVMFuncNamed("defineProperty", vmFunc(func(args ...any) (any, error) { return true, nil })),
		"deleteProperty": nativeVMFuncNamed("deleteProperty", vmFunc(func(args ...any) (any, error) { return true, nil })),
		"apply": nativeVMFuncNamed("apply", vmFunc(func(args ...any) (any, error) {
			if len(args) < 3 {
				return nil, nil
			}
			callArgs, _ := args[2].([]any)
			return s.callFn(args[0], callArgs...)
		})),
		"construct": nativeVMFuncNamed("construct", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return withOrderedKeys(map[string]any{}, []string{}), nil
			}
			callArgs := []any{}
			if len(args) > 1 {
				callArgs, _ = args[1].([]any)
			}
			return s.callFn(args[0], callArgs...)
		})),
		"get": nativeVMFuncNamed("get", vmFunc(func(args ...any) (any, error) {
			if len(args) < 2 {
				return nil, nil
			}
			return s.jsGetProp(args[0], args[1]), nil
		})),
		"getOwnPropertyDescriptor": nativeVMFuncNamed("getOwnPropertyDescriptor", vmFunc(func(args ...any) (any, error) {
			if len(args) < 2 {
				return nil, nil
			}
			value := s.jsGetProp(args[0], args[1])
			return withOrderedKeys(map[string]any{
				"value":        value,
				"writable":     true,
				"enumerable":   true,
				"configurable": true,
			}, []string{"value", "writable", "enumerable", "configurable"}), nil
		})),
		"getPrototypeOf": nativeVMFuncNamed("getPrototypeOf", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return nil, nil
			}
			if target, ok := args[0].(map[string]any); ok {
				if marker, exists := target[prototypeMeta]; exists {
					if boolMarker, ok := marker.(bool); ok && !boolMarker {
						return nil, nil
					}
				}
			}
			return s.jsGetProp(args[0], prototypeMeta), nil
		})),
		"has": nativeVMFuncNamed("has", vmFunc(func(args ...any) (any, error) {
			if len(args) < 2 {
				return false, nil
			}
			return s.jsGetProp(args[0], args[1]) != nil, nil
		})),
		"isExtensible": nativeVMFuncNamed("isExtensible", vmFunc(func(args ...any) (any, error) { return true, nil })),
		"ownKeys": nativeVMFuncNamed("ownKeys", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return []any{}, nil
			}
			return objectOwnPropertyNames(args[0]), nil
		})),
		"preventExtensions": nativeVMFuncNamed("preventExtensions", vmFunc(func(args ...any) (any, error) { return true, nil })),
		"set": nativeVMFuncNamed("set", vmFunc(func(args ...any) (any, error) {
			if len(args) < 3 {
				return true, nil
			}
			s.traceTargetCall("Reflect.set", args[0])
			s.traceDetail("Reflect.set.prop", args[1])
			if target, ok := args[0].(map[string]any); ok {
				if name := strings.TrimSpace(jsonString(target[objectNameMeta])); name != "" && (strings.Contains(name, "HTMLDivElement") || strings.Contains(name, "CSSStyleDeclaration") || strings.Contains(name, "NullProtoObject")) {
					s.traceDetail("Reflect.set.value", args[2])
				}
			}
			return s.jsSetProp(args[0], args[1], args[2]), nil
		})),
		"setPrototypeOf": nativeVMFuncNamed("setPrototypeOf", vmFunc(func(args ...any) (any, error) {
			if len(args) < 2 {
				return true, nil
			}
			if target, ok := args[0].(map[string]any); ok {
				if proto, ok := args[1].(map[string]any); ok {
					target[prototypeMeta] = proto
				}
			}
			return true, nil
		})),
	}, []string{})
	window["Object"] = callableObject("Object", map[string]any{
		"keys": nativeVMFuncNamed("keys", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return []any{}, nil
			}
			s.traceTargetCall("Object.keys", args[0])
			result := objectKeys(args[0])
			s.traceDetail("Object.keys.result", result)
			return result, nil
		})),
		"getOwnPropertyNames": nativeVMFuncNamed("getOwnPropertyNames", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return []any{}, nil
			}
			s.traceTargetCall("Object.getOwnPropertyNames", args[0])
			result := objectOwnPropertyNames(args[0])
			s.traceDetail("Object.getOwnPropertyNames.result", result)
			return result, nil
		})),
		"getPrototypeOf": nativeVMFuncNamed("getPrototypeOf", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return nil, nil
			}
			if target, ok := args[0].(map[string]any); ok {
				if marker, exists := target[prototypeMeta]; exists {
					if boolMarker, ok := marker.(bool); ok && !boolMarker {
						return nil, nil
					}
				}
				return target[prototypeMeta], nil
			}
			return nil, nil
		})),
		"create": nativeVMFuncNamed("create", vmFunc(func(args ...any) (any, error) {
			if len(args) > 0 {
				s.traceTargetCall("Object.create", args[0])
			}
			created := withOrderedKeys(map[string]any{}, []string{})
			s.objectSeq++
			objectName := fmt.Sprintf("ObjectCreate#%d", s.objectSeq)
			if len(args) > 0 {
				switch proto := args[0].(type) {
				case map[string]any:
					created[prototypeMeta] = proto
					if protoName := strings.TrimSpace(jsonString(proto[objectNameMeta])); protoName != "" {
						objectName = fmt.Sprintf("ObjectCreate(%s)#%d", protoName, s.objectSeq)
					}
				case nil:
					created[prototypeMeta] = false
					objectName = fmt.Sprintf("NullProtoObject#%d", s.objectSeq)
				}
			}
			created[objectNameMeta] = objectName
			s.traceDetail("Object.create.proto", created[prototypeMeta])
			return created, nil
		})),
	}, nil)
	randomIndex := 0
	window["Math"] = namedObject("Math", map[string]any{
		"random": nativeVMFuncNamed("random", vmFunc(func(args ...any) (any, error) {
			if randomIndex < len(mathRandomSequence) {
				value := mathRandomSequence[randomIndex]
				randomIndex++
				return value, nil
			}
			return rand.Float64(), nil
		})),
		"abs": nativeVMFuncNamed("abs", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return float64(0), nil
			}
			n, _ := s.asNumber(args[0])
			return math.Abs(n), nil
		})),
		"floor": nativeVMFuncNamed("floor", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return float64(0), nil
			}
			n, _ := s.asNumber(args[0])
			return math.Floor(n), nil
		})),
		"ceil": nativeVMFuncNamed("ceil", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return float64(0), nil
			}
			n, _ := s.asNumber(args[0])
			return math.Ceil(n), nil
		})),
		"round": nativeVMFuncNamed("round", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return float64(0), nil
			}
			n, _ := s.asNumber(args[0])
			return math.Round(n), nil
		})),
		"max": nativeVMFuncNamed("max", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return math.Inf(-1), nil
			}
			best := math.Inf(-1)
			for _, arg := range args {
				n, _ := s.asNumber(arg)
				if n > best {
					best = n
				}
			}
			return best, nil
		})),
		"min": nativeVMFuncNamed("min", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return math.Inf(1), nil
			}
			best := math.Inf(1)
			for _, arg := range args {
				n, _ := s.asNumber(arg)
				if n < best {
					best = n
				}
			}
			return best, nil
		})),
		"pow": nativeVMFuncNamed("pow", vmFunc(func(args ...any) (any, error) {
			if len(args) < 2 {
				return float64(1), nil
			}
			left, _ := s.asNumber(args[0])
			right, _ := s.asNumber(args[1])
			return math.Pow(left, right), nil
		})),
		"sign": nativeVMFuncNamed("sign", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return float64(0), nil
			}
			n, _ := s.asNumber(args[0])
			switch {
			case math.IsNaN(n):
				return math.NaN(), nil
			case n > 0:
				return float64(1), nil
			case n < 0:
				return float64(-1), nil
			default:
				return float64(0), nil
			}
		})),
	}, []string{})
	window["JSON"] = namedObject("JSON", map[string]any{
		"parse": nativeVMFuncNamed("parse", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return nil, nil
			}
			var out any
			if err := json.Unmarshal([]byte(s.jsToString(args[0])), &out); err != nil {
				return nil, err
			}
			return out, nil
		})),
		"stringify": nativeVMFuncNamed("stringify", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return "null", nil
			}
			body, err := jsJSONStringify(args[0])
			if err != nil {
				return nil, err
			}
			return body, nil
		})),
		"rawJSON": nativeVMFuncNamed("rawJSON", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return nil, nil
			}
			return s.jsToString(args[0]), nil
		})),
		"isRawJSON": nativeVMFuncNamed("isRawJSON", vmFunc(func(args ...any) (any, error) {
			return false, nil
		})),
	}, []string{})
	window["atob"] = nativeVMFuncNamed("atob", vmFunc(func(args ...any) (any, error) {
		if len(args) == 0 {
			return "", nil
		}
		return latin1Base64Decode(s.jsToString(args[0]))
	}))
	window["btoa"] = nativeVMFuncNamed("btoa", vmFunc(func(args ...any) (any, error) {
		if len(args) == 0 {
			return "", nil
		}
		return latin1Base64Encode(s.jsToString(args[0])), nil
	}))
	window["localStorage"] = localStorage
	window["document"] = document
	window["navigator"] = navigator
	window["screen"] = screen
	window["location"] = location
	window["history"] = namedObject("history", map[string]any{"length": float64(2)}, []string{})
	performanceProto := withOwnPropertyNames(namedObject("Performance.prototype", map[string]any{
		"onresourcetimingbufferfull": nil,
		"clearMarks": nativeVMFuncNamed("clearMarks", vmFunc(func(args ...any) (any, error) { return nil, nil })),
		"clearMeasures": nativeVMFuncNamed("clearMeasures", vmFunc(func(args ...any) (any, error) { return nil, nil })),
		"clearResourceTimings": nativeVMFuncNamed("clearResourceTimings", vmFunc(func(args ...any) (any, error) { return nil, nil })),
		"getEntries": nativeVMFuncNamed("getEntries", vmFunc(func(args ...any) (any, error) { return []any{}, nil })),
		"getEntriesByName": nativeVMFuncNamed("getEntriesByName", vmFunc(func(args ...any) (any, error) { return []any{}, nil })),
		"getEntriesByType": nativeVMFuncNamed("getEntriesByType", vmFunc(func(args ...any) (any, error) { return []any{}, nil })),
		"mark": nativeVMFuncNamed("mark", vmFunc(func(args ...any) (any, error) { return nil, nil })),
		"measure": nativeVMFuncNamed("measure", vmFunc(func(args ...any) (any, error) { return nil, nil })),
		"setResourceTimingBufferSize": nativeVMFuncNamed("setResourceTimingBufferSize", vmFunc(func(args ...any) (any, error) { return nil, nil })),
		"toJSON": nativeVMFuncNamed("toJSON", vmFunc(func(args ...any) (any, error) { return withOrderedKeys(map[string]any{}, []string{}), nil })),
		"now": nativeVMFuncNamed("now", vmFunc(func(args ...any) (any, error) {
			elapsedMs := float64(time.Since(start).Milliseconds())
			return performanceNow + elapsedMs, nil
		})),
		"constructor": nil,
		"timing": nil,
		"navigation": nil,
		"memory": withOrderedKeys(map[string]any{
			"jsHeapSizeLimit": float64(heapLimit),
		}, []string{}),
		"eventCounts": nil,
		"interactionCount": nil,
	}, []string{"timeOrigin", "onresourcetimingbufferfull", "clearMarks", "clearMeasures", "clearResourceTimings", "getEntries", "getEntriesByName", "getEntriesByType", "mark", "measure", "setResourceTimingBufferSize", "toJSON", "now", "constructor", "timing", "navigation", "memory", "eventCounts", "interactionCount"}), authPerformancePrototypeOwnPropertyNames)
	window["performance"] = withOwnPropertyNames(namedObject("performance", map[string]any{
		"timeOrigin": timeOrigin,
		"memory":     performanceProto["memory"],
	}, []string{"timeOrigin"}), []string{"timeOrigin"})
	window["performance"].(map[string]any)[prototypeMeta] = performanceProto
	window["Array"] = callableObject("Array", map[string]any{
		"length": float64(1),
		"from": nativeVMFuncNamed("from", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return []any{}, nil
			}
			switch value := args[0].(type) {
			case []any:
				return append([]any{}, value...), nil
			case []string:
				out := make([]any, 0, len(value))
				for _, item := range value {
					out = append(out, item)
				}
				return out, nil
			case string:
				out := make([]any, 0, len(value))
				for _, ch := range value {
					out = append(out, string(ch))
				}
				return out, nil
			case map[string]any:
				length := toIntIndex(s.jsGetProp(value, "length"))
				if length < 0 {
					return []any{}, nil
				}
				out := make([]any, 0, length)
				for idx := 0; idx < length; idx++ {
					out = append(out, s.jsGetProp(value, float64(idx)))
				}
				return out, nil
			default:
				return []any{}, nil
			}
		})),
		"isArray": nativeVMFuncNamed("isArray", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return false, nil
			}
			switch args[0].(type) {
			case []any, []string:
				return true, nil
			default:
				return false, nil
			}
		})),
		"fromAsync": nativeVMFuncNamed("fromAsync", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return []any{}, nil
			}
			return s.callFn(window["Array"].(map[string]any)["from"], args...)
		})),
		"of": nativeVMFuncNamed("of", vmFunc(func(args ...any) (any, error) {
			return append([]any{}, args...), nil
		})),
	}, nil)
	window["Function"] = callableObject("Function", map[string]any{}, nil)
	window["Number"] = callableObject("Number", map[string]any{}, nil)
	window["Boolean"] = callableObject("Boolean", map[string]any{}, nil)
	window["String"] = callableObject("String", map[string]any{}, nil)
	window["Symbol"] = callableObject("Symbol", map[string]any{}, nil)
	window["Date"] = callableObject("Date", map[string]any{
		"length": float64(7),
		"now": nativeVMFuncNamed("now", vmFunc(func(args ...any) (any, error) {
			return float64(time.Now().UnixMilli()), nil
		})),
		"parse": nativeVMFuncNamed("parse", vmFunc(func(args ...any) (any, error) {
			if len(args) == 0 {
				return math.NaN(), nil
			}
			text := strings.TrimSpace(s.jsToString(args[0]))
			if text == "" {
				return math.NaN(), nil
			}
			if parsed, err := time.Parse(time.RFC3339, text); err == nil {
				return float64(parsed.UnixMilli()), nil
			}
			if parsed, err := time.Parse(time.RFC1123Z, text); err == nil {
				return float64(parsed.UnixMilli()), nil
			}
			if parsed, err := time.Parse("Mon Jan 02 2006 15:04:05 GMT-0700 (MST)", text); err == nil {
				return float64(parsed.UnixMilli()), nil
			}
			return math.NaN(), nil
		})),
		"UTC": nativeVMFuncNamed("UTC", vmFunc(func(args ...any) (any, error) {
			parts := []int{1970, 0, 1, 0, 0, 0, 0}
			for idx := 0; idx < len(args) && idx < len(parts); idx++ {
				if parsed := toIntIndex(args[idx]); parsed >= 0 {
					parts[idx] = parsed
				}
			}
			utc := time.Date(parts[0], time.Month(parts[1]+1), parts[2], parts[3], parts[4], parts[5], parts[6]*1_000_000, time.UTC)
			return float64(utc.UnixMilli()), nil
		})),
	}, nil)
	window["Promise"] = callableObject("Promise", map[string]any{}, nil)
	window["RegExp"] = callableObject("RegExp", map[string]any{}, nil)
	window["Error"] = callableObject("Error", map[string]any{}, nil)
	window["AggregateError"] = callableObject("AggregateError", map[string]any{}, nil)
	window["EvalError"] = callableObject("EvalError", map[string]any{}, nil)
	window["RangeError"] = callableObject("RangeError", map[string]any{}, nil)
	window["ReferenceError"] = callableObject("ReferenceError", map[string]any{}, nil)
	window["SyntaxError"] = callableObject("SyntaxError", map[string]any{}, nil)
	window["TypeError"] = callableObject("TypeError", map[string]any{}, nil)
	window["URIError"] = callableObject("URIError", map[string]any{}, nil)
	window["ArrayBuffer"] = callableObject("ArrayBuffer", map[string]any{}, nil)
	window["Uint8Array"] = callableObject("Uint8Array", map[string]any{}, nil)
	window["Int8Array"] = callableObject("Int8Array", map[string]any{}, nil)
	window["Uint16Array"] = callableObject("Uint16Array", map[string]any{}, nil)
	window["Int16Array"] = callableObject("Int16Array", map[string]any{}, nil)
	window["Uint32Array"] = callableObject("Uint32Array", map[string]any{}, nil)
	window["Int32Array"] = callableObject("Int32Array", map[string]any{}, nil)
	window["BigUint64Array"] = callableObject("BigUint64Array", map[string]any{}, nil)
	window["BigInt64Array"] = callableObject("BigInt64Array", map[string]any{}, nil)
	window["Uint8ClampedArray"] = callableObject("Uint8ClampedArray", map[string]any{}, nil)
	window["Float32Array"] = callableObject("Float32Array", map[string]any{}, nil)
	window["Float64Array"] = callableObject("Float64Array", map[string]any{}, nil)
	window["DataView"] = callableObject("DataView", map[string]any{}, nil)
	window["Map"] = callableObject("Map", map[string]any{}, nil)
	window["BigInt"] = callableObject("BigInt", map[string]any{}, nil)
	window["Set"] = callableObject("Set", map[string]any{}, nil)
	window["Iterator"] = callableObject("Iterator", map[string]any{}, nil)
	window["WeakMap"] = callableObject("WeakMap", map[string]any{}, nil)
	window["WeakSet"] = callableObject("WeakSet", map[string]any{}, nil)
	window["Proxy"] = callableObject("Proxy", map[string]any{}, nil)
	window["FinalizationRegistry"] = callableObject("FinalizationRegistry", map[string]any{}, nil)
	window["WeakRef"] = callableObject("WeakRef", map[string]any{}, nil)
	window["parseFloat"] = callableObject("parseFloat", map[string]any{}, vmFunc(func(args ...any) (any, error) {
		if len(args) == 0 {
			return math.NaN(), nil
		}
		value, err := strconv.ParseFloat(strings.TrimSpace(s.jsToString(args[0])), 64)
		if err != nil {
			return math.NaN(), nil
		}
		return value, nil
	}))
	window["parseInt"] = callableObject("parseInt", map[string]any{}, vmFunc(func(args ...any) (any, error) {
		if len(args) == 0 {
			return math.NaN(), nil
		}
		text := strings.TrimSpace(s.jsToString(args[0]))
		if text == "" {
			return math.NaN(), nil
		}
		base := 10
		if len(args) > 1 {
			if numericBase := toIntIndex(args[1]); numericBase > 0 {
				base = numericBase
			}
		}
		value, err := strconv.ParseInt(text, base, 64)
		if err != nil {
			return math.NaN(), nil
		}
		return float64(value), nil
	}))
	window["Infinity"] = math.Inf(1)
	window["NaN"] = math.NaN()
	window["undefined"] = nil
	window["Atomics"] = withOrderedKeys(map[string]any{}, []string{})
	window["Intl"] = withOrderedKeys(map[string]any{}, []string{})
	window["decodeURI"] = callableObject("decodeURI", map[string]any{}, nil)
	window["decodeURIComponent"] = callableObject("decodeURIComponent", map[string]any{}, nil)
	window["encodeURI"] = callableObject("encodeURI", map[string]any{}, nil)
	window["encodeURIComponent"] = callableObject("encodeURIComponent", map[string]any{}, nil)
	window["escape"] = callableObject("escape", map[string]any{}, nil)
	window["unescape"] = callableObject("unescape", map[string]any{}, nil)
	window["eval"] = callableObject("eval", map[string]any{}, nil)
	window["isFinite"] = callableObject("isFinite", map[string]any{}, nil)
	window["isNaN"] = callableObject("isNaN", map[string]any{}, nil)
	window["console"] = withOrderedKeys(map[string]any{}, []string{})
	window["Option"] = callableObject("Option", map[string]any{}, nil)
	window["Image"] = callableObject("Image", map[string]any{}, nil)
	window["Audio"] = callableObject("Audio", map[string]any{}, nil)
	window["webkitURL"] = callableObject("URL", map[string]any{}, nil)
	window["webkitRTCPeerConnection"] = callableObject("RTCPeerConnection", map[string]any{}, nil)
	window["webkitMediaStream"] = callableObject("MediaStream", map[string]any{}, nil)
	window["WebKitMutationObserver"] = callableObject("MutationObserver", map[string]any{}, nil)
	window["WebKitCSSMatrix"] = callableObject("DOMMatrix", map[string]any{}, nil)
	window["XPathResult"] = callableObject("XPathResult", map[string]any{}, nil)
	window["XPathExpression"] = callableObject("XPathExpression", map[string]any{}, nil)
	window["XPathEvaluator"] = callableObject("XPathEvaluator", map[string]any{}, nil)
	window["XMLSerializer"] = callableObject("XMLSerializer", map[string]any{}, nil)
	window["XMLHttpRequestUpload"] = callableObject("XMLHttpRequestUpload", map[string]any{}, nil)
	window["XMLHttpRequestEventTarget"] = callableObject("XMLHttpRequestEventTarget", map[string]any{}, nil)
	window["XMLHttpRequest"] = callableObject("XMLHttpRequest", map[string]any{}, nil)
	window["XMLDocument"] = callableObject("XMLDocument", map[string]any{}, nil)
	window["WritableStreamDefaultWriter"] = callableObject("WritableStreamDefaultWriter", map[string]any{}, nil)
	window["WritableStreamDefaultController"] = callableObject("WritableStreamDefaultController", map[string]any{}, nil)
	window["WritableStream"] = callableObject("WritableStream", map[string]any{}, nil)
	window["Worker"] = callableObject("Worker", map[string]any{}, nil)
	window["Window"] = callableObject("Window", map[string]any{}, nil)
	window["WheelEvent"] = callableObject("WheelEvent", map[string]any{}, nil)
	window["WebSocket"] = callableObject("WebSocket", map[string]any{}, nil)
	window["WebGLVertexArrayObject"] = callableObject("WebGLVertexArrayObject", map[string]any{}, nil)
	window["WebGLUniformLocation"] = callableObject("WebGLUniformLocation", map[string]any{}, nil)
	window["WebGLTransformFeedback"] = callableObject("WebGLTransformFeedback", map[string]any{}, nil)
	window["WebGLTexture"] = callableObject("WebGLTexture", map[string]any{}, nil)
	window["WebGLSync"] = callableObject("WebGLSync", map[string]any{}, nil)
	window["WebGLShaderPrecisionFormat"] = callableObject("WebGLShaderPrecisionFormat", map[string]any{}, nil)
	window["WebGLShader"] = callableObject("WebGLShader", map[string]any{}, nil)
	window["WebGLSampler"] = callableObject("WebGLSampler", map[string]any{}, nil)
	window["WebGLRenderingContext"] = callableObject("WebGLRenderingContext", map[string]any{}, nil)
	window["WebGLRenderbuffer"] = callableObject("WebGLRenderbuffer", map[string]any{}, nil)
	window["WebGLQuery"] = callableObject("WebGLQuery", map[string]any{}, nil)
	window["WebGLProgram"] = callableObject("WebGLProgram", map[string]any{}, nil)
	window["WebGLObject"] = callableObject("WebGLObject", map[string]any{}, nil)
	window["WebGLFramebuffer"] = callableObject("WebGLFramebuffer", map[string]any{}, nil)
	window["WebGLContextEvent"] = callableObject("WebGLContextEvent", map[string]any{}, nil)
	window["WebGLBuffer"] = callableObject("WebGLBuffer", map[string]any{}, nil)
	window["WebGLActiveInfo"] = callableObject("WebGLActiveInfo", map[string]any{}, nil)
	window["WebGL2RenderingContext"] = callableObject("WebGL2RenderingContext", map[string]any{}, nil)
	window["WaveShaperNode"] = callableObject("WaveShaperNode", map[string]any{}, nil)
	window["VisualViewport"] = callableObject("VisualViewport", map[string]any{}, nil)
	window["VisibilityStateEntry"] = callableObject("VisibilityStateEntry", map[string]any{}, nil)
	window["VirtualKeyboardGeometryChangeEvent"] = callableObject("VirtualKeyboardGeometryChangeEvent", map[string]any{}, nil)
	window["ViewTransitionTypeSet"] = callableObject("ViewTransitionTypeSet", map[string]any{}, nil)
	window["ViewTransition"] = callableObject("ViewTransition", map[string]any{}, nil)
	window["ViewTimeline"] = callableObject("ViewTimeline", map[string]any{}, nil)
	window["VideoPlaybackQuality"] = callableObject("VideoPlaybackQuality", map[string]any{}, nil)
	window["VideoFrame"] = callableObject("VideoFrame", map[string]any{}, nil)
	window["VideoColorSpace"] = callableObject("VideoColorSpace", map[string]any{}, nil)
	window["ValidityState"] = callableObject("ValidityState", map[string]any{}, nil)
	window["VTTCue"] = callableObject("VTTCue", map[string]any{}, nil)
	window["UserActivation"] = callableObject("UserActivation", map[string]any{}, nil)
	window["URLSearchParams"] = callableObject("URLSearchParams", map[string]any{}, nil)
	window["URLPattern"] = callableObject("URLPattern", map[string]any{}, nil)
	window["0"] = window
	window["innerWidth"] = float64(innerWidth)
	window["innerHeight"] = float64(innerHeight)
	window["outerWidth"] = float64(outerWidth)
	window["outerHeight"] = float64(outerHeight)
	window["screenX"] = float64(screenX)
	window["screenY"] = float64(screenY)
	window["scrollX"] = float64(0)
	window["pageXOffset"] = float64(0)
	window["scrollY"] = float64(0)
	window["pageYOffset"] = float64(0)
	window["devicePixelRatio"] = 1.0000000149011612
	window["hardwareConcurrency"] = float64(hardwareConcurrency)
	window["isSecureContext"] = true
	window["crossOriginIsolated"] = false
	window["event"] = nil
	window["clientInformation"] = navigator
	window["screenLeft"] = float64(screenX)
	window["screenTop"] = float64(screenY)
	window["chrome"] = withOrderedKeys(map[string]any{"runtime": withOrderedKeys(map[string]any{}, []string{})}, []string{})
	window["crashReport"] = nil
	window["close"] = vmFunc(func(args ...any) (any, error) { return nil, nil })
	window["__reactRouterContext"] = withOrderedKeys(map[string]any{
		"basename": "",
		"future": withOrderedKeys(map[string]any{
			"v8_middleware":                 false,
			"unstable_optimizeDeps":         true,
			"unstable_splitRouteModules":    false,
			"unstable_subResourceIntegrity": false,
			"unstable_viteEnvironmentApi":   false,
		}, []string{
			"v8_middleware",
			"unstable_optimizeDeps",
			"unstable_splitRouteModules",
			"unstable_subResourceIntegrity",
			"unstable_viteEnvironmentApi",
		}),
		"routeDiscovery": withOrderedKeys(map[string]any{
			"mode": "initial",
		}, []string{"mode"}),
		"ssr":       true,
		"isSpaMode": false,
		"streamController": withOrderedKeys(map[string]any{}, []string{}),
		"stream":          nil,
		"state": withOrderedKeys(map[string]any{
			"loaderData": withOrderedKeys(map[string]any{
				"routes/layouts/client-auth-session-layout/layout": withOrderedKeys(map[string]any{
					"session": withOrderedKeys(map[string]any{
						"is_missing_session": true,
					}, []string{"is_missing_session"}),
					"seedCacheEntry": nil,
				}, []string{"session", "seedCacheEntry"}),
			}, []string{"routes/layouts/client-auth-session-layout/layout"}),
			"actionData": nil,
			"errors":     nil,
		}, []string{"loaderData", "actionData", "errors"}),
	}, []string{
		"basename",
		"future",
		"routeDiscovery",
		"ssr",
		"isSpaMode",
		"streamController",
		"stream",
		"state",
	})
	window["$RB"] = []any{}
	window["$RV"] = vmFunc(func(args ...any) (any, error) { return nil, nil })
	window["$RC"] = vmFunc(func(args ...any) (any, error) { return nil, nil })
	window["$RT"] = performanceNow
	window["ret_nodes"] = []any{}
	window["__reactRouterManifest"] = withOrderedKeys(map[string]any{}, []string{})
	window["__STATSIG__"] = withOrderedKeys(map[string]any{}, []string{})
	window["__reactRouterVersion"] = "7.9.3"
	window["__REACT_INTL_CONTEXT__"] = withOrderedKeys(map[string]any{}, []string{})
	window["DD_RUM"] = withOrderedKeys(map[string]any{}, []string{})
	window["__SEGMENT_INSPECTOR__"] = withOrderedKeys(map[string]any{}, []string{})
	window["__reactRouterRouteModules"] = withOrderedKeys(map[string]any{}, []string{})
	window["__reactRouterDataRouter"] = withOrderedKeys(map[string]any{}, []string{})
	window["__sentinel_token_pending"] = withOrderedKeys(map[string]any{}, []string{})
	window["__sentinel_init_pending"] = withOrderedKeys(map[string]any{}, []string{})
	window["SentinelSDK"] = withOrderedKeys(map[string]any{}, []string{})
	window["cdc_adoQpoasnfa76pfcZLmcfl_Array"] = window["Array"]
	window["cdc_adoQpoasnfa76pfcZLmcfl_Object"] = window["Object"]
	window["cdc_adoQpoasnfa76pfcZLmcfl_Promise"] = window["Promise"]
	window["cdc_adoQpoasnfa76pfcZLmcfl_Proxy"] = window["Proxy"]
	window["cdc_adoQpoasnfa76pfcZLmcfl_Symbol"] = window["Symbol"]
	window["cdc_adoQpoasnfa76pfcZLmcfl_JSON"] = window["JSON"]
	window["cdc_adoQpoasnfa76pfcZLmcfl_Window"] = window["Window"]
	window[windowProbe] = nil
	window["close"] = nativeVMFuncNamed("close", vmFunc(func(args ...any) (any, error) { return nil, nil }))
	appendOrderedKey(window, windowProbe)
	document[documentProbe] = true
	document = withOwnPropertyNames(document, []string{documentProbe, "location", "createElement", reactContainerKey, secondaryDocumentProbe})
	window["document"] = document
	window["window"] = window
	window["self"] = window
	window["globalThis"] = window
	for _, key := range authWindowOwnPropertyNames {
		if _, exists := window[key]; !exists {
			window[key] = nil
		}
	}
	for _, key := range authWindowKeyOrder {
		if _, exists := window[key]; !exists {
			window[key] = nil
		}
	}
	return window
}

func (s *turnstileSolver) runQueue() error {
	for !s.done {
		queue, ok := s.getReg(turnstileQueueReg).([]any)
		if !ok || len(queue) == 0 {
			return nil
		}
		ins, ok := queue[0].([]any)
		s.setReg(turnstileQueueReg, queue[1:])
		if !ok || len(ins) == 0 {
			continue
		}
		fn, ok := s.getReg(ins[0]).(vmFunc)
		if !ok {
			return fmt.Errorf("vm opcode not callable: %v", ins[0])
		}
		if _, err := fn(ins[1:]...); err != nil {
			return err
		}
		s.stepCount++
		if s.stepCount > s.maxSteps {
			return fmt.Errorf("turnstile vm step overflow")
		}
	}
	return nil
}

func (s *turnstileSolver) callFn(value any, args ...any) (any, error) {
	switch fn := value.(type) {
	case vmFunc:
		s.traceCall("vmFunc")
		return fn(args...)
	case nativeVMFunc:
		s.traceCall(fn.name)
		return fn.fn(args...)
	case stringifiedVMFunc:
		s.traceCall(fn.source)
		return fn.fn(args...)
	case map[string]any:
		if callable, ok := fn[callMeta]; ok {
			s.traceCall(strings.TrimSpace(jsonString(fn[functionNameMeta])))
			return s.callFn(callable, args...)
		}
		return nil, nil
	default:
		return nil, nil
	}
}

func (s *turnstileSolver) derefArgs(args []any) []any {
	out := make([]any, 0, len(args))
	for _, arg := range args {
		out = append(out, s.getReg(arg))
	}
	return out
}

func (s *turnstileSolver) getReg(key any) any {
	return s.regs[regKey(key)]
}

func (s *turnstileSolver) setReg(key any, value any) {
	s.regs[regKey(key)] = value
}

func (s *turnstileSolver) copyQueue() []any {
	queue, _ := s.getReg(turnstileQueueReg).([]any)
	return copyAnySlice(queue)
}

func (s *turnstileSolver) asNumber(value any) (float64, bool) {
	switch v := value.(type) {
	case nil:
		return math.NaN(), false
	case bool:
		if v {
			return 1, true
		}
		return 0, true
	case int:
		return float64(v), true
	case int64:
		return float64(v), true
	case float64:
		return v, true
	case string:
		if strings.TrimSpace(v) == "" {
			return 0, true
		}
		parsed, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
		if err != nil {
			return math.NaN(), false
		}
		return parsed, true
	default:
		return math.NaN(), false
	}
}

func (s *turnstileSolver) valuesEqual(left, right any) bool {
	switch l := left.(type) {
	case float64:
		r, ok := right.(float64)
		return ok && l == r
	case string:
		r, ok := right.(string)
		return ok && l == r
	case bool:
		r, ok := right.(bool)
		return ok && l == r
	case nil:
		return right == nil
	default:
		lv := reflect.ValueOf(left)
		rv := reflect.ValueOf(right)
		if lv.IsValid() && rv.IsValid() && lv.Type() == rv.Type() {
			switch lv.Kind() {
			case reflect.Map, reflect.Slice, reflect.Func, reflect.Pointer:
				return lv.Pointer() == rv.Pointer()
			}
		}
		return fmt.Sprintf("%v", left) == fmt.Sprintf("%v", right)
	}
}

func (s *turnstileSolver) jsToString(value any) string {
	switch v := value.(type) {
	case nil:
		return "undefined"
	case bool:
		if v {
			return "true"
		}
		return "false"
	case int:
		return strconv.Itoa(v)
	case int64:
		return strconv.FormatInt(v, 10)
	case float64:
		if math.IsNaN(v) {
			return "NaN"
		}
		if math.IsInf(v, 1) {
			return "Infinity"
		}
		if math.IsInf(v, -1) {
			return "-Infinity"
		}
		if math.Trunc(v) == v {
			return strconv.FormatInt(int64(v), 10)
		}
		return strconv.FormatFloat(v, 'f', -1, 64)
	case string:
		return v
	case nativeVMFunc:
		name := strings.TrimSpace(v.name)
		if name == "" {
			name = "anonymous"
		}
		return fmt.Sprintf("function %s() { [native code] }", name)
	case stringifiedVMFunc:
		if strings.TrimSpace(v.source) != "" {
			return v.source
		}
		return "function () { [native code] }"
	case []any:
		parts := make([]string, 0, len(v))
		for _, item := range v {
			parts = append(parts, s.jsToStringArrayItem(item))
		}
		return strings.Join(parts, ",")
	case map[string]any:
		if source := strings.TrimSpace(jsonString(v[functionSourceMeta])); source != "" {
			return source
		}
		if name := strings.TrimSpace(jsonString(v[functionNameMeta])); name != "" {
			return fmt.Sprintf("function %s() { [native code] }", name)
		}
		if href, ok := v["href"].(string); ok && v["search"] != nil {
			return href
		}
		return "[object Object]"
	default:
		return fmt.Sprintf("%v", v)
	}
}

func (s *turnstileSolver) jsToStringArrayItem(value any) string {
	if value == nil {
		return ""
	}
	return s.jsToString(value)
}

func (s *turnstileSolver) jsGetProp(obj any, prop any) any {
	switch value := obj.(type) {
	case nil:
		return nil
	case regMapRef:
		return value.solver.getReg(prop)
	case map[string]any:
		propKey := s.jsToString(prop)
		s.traceProp(value, propKey)
		if storage, ok := value["__storage_data__"].(map[string]any); ok {
			switch propKey {
			case "__storage_data__", "__storage_keys__", "length", "key", "getItem", "setItem", "removeItem", "clear":
				if direct, exists := value[propKey]; exists {
					return direct
				}
				if propKey == "length" {
					return float64(len(keysOfMap(storage)))
				}
				if proto, ok := value[prototypeMeta].(map[string]any); ok {
					return s.jsGetProp(proto, propKey)
				}
				return nil
			default:
				if item, ok := value[propKey]; ok {
					return item
				}
				return storage[propKey]
			}
		}
		if direct, ok := value[propKey]; ok {
			return direct
		}
		if proto, ok := value[prototypeMeta].(map[string]any); ok {
			return s.jsGetProp(proto, propKey)
		}
		return nil
	case []any:
		if s.jsToString(prop) == "length" {
			return float64(len(value))
		}
		index := toIntIndex(prop)
		if index < 0 || index >= len(value) {
			return nil
		}
		return value[index]
	case []string:
		if s.jsToString(prop) == "length" {
			return float64(len(value))
		}
		index := toIntIndex(prop)
		if index < 0 || index >= len(value) {
			return nil
		}
		return value[index]
	case string:
		if s.jsToString(prop) == "length" {
			return float64(len(value))
		}
		index := toIntIndex(prop)
		if index < 0 || index >= len(value) {
			return nil
		}
		return string(value[index])
	default:
		return nil
	}
}

func (s *turnstileSolver) jsSetProp(obj any, prop any, value any) bool {
	switch target := obj.(type) {
	case regMapRef:
		target.solver.setReg(prop, value)
		return true
	case map[string]any:
		propKey := s.jsToString(prop)
		if storage, ok := target["__storage_data__"].(map[string]any); ok {
			switch propKey {
			case "__storage_data__", "__storage_keys__", "length", "key", "getItem", "setItem", "removeItem", "clear":
				target[propKey] = value
			default:
				storage[propKey] = value
				target[propKey] = value
				target["__storage_keys__"] = keysOfMap(storage)
				target["length"] = float64(len(keysOfMap(storage)))
			}
			return true
		}
		target[propKey] = value
		appendOrderedKey(target, propKey)
		return true
	default:
		return false
	}
}

func objectKeys(value any) []any {
	switch obj := value.(type) {
	case map[string]any:
		if storage, ok := obj["__storage_data__"].(map[string]any); ok {
			out := make([]any, 0, len(storage)+1)
			if ordered, ok := obj["__storage_enumerable_keys__"].([]string); ok {
				for _, key := range ordered {
					if key == "setItem" {
						out = append(out, key)
						continue
					}
					if _, exists := storage[key]; !exists {
						continue
					}
					out = append(out, key)
				}
				return out
			}
			for _, key := range keysOfMap(storage) {
				out = append(out, key)
			}
			return out
		}
		if keys, ok := obj[orderedKeysMeta].([]string); ok {
			out := make([]any, 0, len(keys))
			for _, key := range keys {
				if isInternalMetaKey(key) {
					continue
				}
				out = append(out, key)
			}
			return out
		}
		keys := keysOfMap(obj)
		out := make([]any, 0, len(keys))
		for _, key := range keys {
			if isInternalMetaKey(key) {
				continue
			}
			out = append(out, key)
		}
		return out
	case []any:
		out := make([]any, 0, len(obj))
		for idx := range obj {
			out = append(out, float64(idx))
		}
		return out
	default:
		return []any{}
	}
}

func objectOwnPropertyNames(value any) []any {
	switch obj := value.(type) {
	case map[string]any:
		if storage, ok := obj["__storage_data__"].(map[string]any); ok {
			out := make([]any, 0, len(storage)+1)
			if ordered, ok := obj["__storage_enumerable_keys__"].([]string); ok {
				for _, key := range ordered {
					if key == "setItem" {
						out = append(out, key)
						continue
					}
					if _, exists := storage[key]; !exists {
						continue
					}
					out = append(out, key)
				}
				return out
			}
			for _, key := range keysOfMap(storage) {
				out = append(out, key)
			}
			return out
		}
		if names, ok := obj[ownPropertyNamesMeta].([]string); ok {
			out := make([]any, 0, len(names))
			for _, key := range names {
				if isInternalMetaKey(key) {
					continue
				}
				out = append(out, key)
			}
			return out
		}
		return objectKeys(value)
	case []any:
		out := objectKeys(value)
		return append(out, "length")
	case []string:
		out := objectKeys(value)
		return append(out, "length")
	case string:
		out := make([]any, 0, len(obj)+1)
		for idx := range obj {
			out = append(out, float64(idx))
		}
		out = append(out, "length")
		return out
	default:
		return []any{}
	}
}

func isCallableJSValue(value any) bool {
	switch v := value.(type) {
	case vmFunc, nativeVMFunc, stringifiedVMFunc:
		return true
	case map[string]any:
		_, ok := v[callMeta]
		return ok
	default:
		return false
	}
}

func mergeOrderedKeys(preferred []string, fallback []string) []string {
	out := make([]string, 0, len(preferred)+len(fallback))
	seen := map[string]struct{}{}
	for _, key := range preferred {
		key = strings.TrimSpace(key)
		if key == "" || isInternalMetaKey(key) {
			continue
		}
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		out = append(out, key)
	}
	for _, key := range fallback {
		key = strings.TrimSpace(key)
		if key == "" || isInternalMetaKey(key) {
			continue
		}
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		out = append(out, key)
	}
	return out
}

func keysOfMap(value map[string]any) []string {
	if keys, ok := value[orderedKeysMeta].([]string); ok {
		return append([]string{}, keys...)
	}
	out := make([]string, 0, len(value))
	for key := range value {
		if isInternalMetaKey(key) {
			continue
		}
		out = append(out, key)
	}
	sort.Strings(out)
	return out
}

func ownPropertyNamesOfMap(value map[string]any) []string {
	if names, ok := value[ownPropertyNamesMeta].([]string); ok {
		return append([]string{}, names...)
	}
	return keysOfMap(value)
}

func withOrderedKeys(value map[string]any, keys []string) map[string]any {
	if value == nil {
		value = map[string]any{}
	}
	ordered := make([]string, 0, len(keys)+len(value))
	seen := map[string]struct{}{}
	for _, key := range keys {
		if isInternalMetaKey(key) {
			continue
		}
		if _, ok := seen[key]; ok {
			continue
		}
		ordered = append(ordered, key)
		seen[key] = struct{}{}
	}
	existing := make([]string, 0, len(value))
	for key := range value {
		if isInternalMetaKey(key) {
			continue
		}
		if _, ok := seen[key]; ok {
			continue
		}
		existing = append(existing, key)
	}
	sort.Strings(existing)
	ordered = append(ordered, existing...)
	value[orderedKeysMeta] = ordered
	return value
}

func withOwnPropertyNames(value map[string]any, names []string) map[string]any {
	if value == nil {
		value = map[string]any{}
	}
	value[ownPropertyNamesMeta] = mergeOrderedKeys(names, ownPropertyNamesOfMap(value))
	return value
}

func appendOrderedKey(value map[string]any, key string) {
	if isInternalMetaKey(key) {
		return
	}
	keys, ok := value[orderedKeysMeta].([]string)
	if !ok {
		value[orderedKeysMeta] = []string{key}
		return
	}
	for _, existing := range keys {
		if existing == key {
			return
		}
	}
	value[orderedKeysMeta] = append(keys, key)
}

func isInternalMetaKey(key string) bool {
	switch key {
	case orderedKeysMeta, ownPropertyNamesMeta, prototypeMeta, callMeta, functionNameMeta, functionSourceMeta, "__storage_data__", "__storage_keys__", "__storage_enumerable_keys__":
		return true
	default:
		return false
	}
}

func jsJSONStringify(value any) (string, error) {
	switch v := value.(type) {
	case nil:
		return "null", nil
	case bool:
		if v {
			return "true", nil
		}
		return "false", nil
	case string:
		body, err := json.Marshal(v)
		return string(body), err
	case int:
		return strconv.Itoa(v), nil
	case int64:
		return strconv.FormatInt(v, 10), nil
	case float64:
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return "null", nil
		}
		body, err := json.Marshal(v)
		return string(body), err
	case []any:
		parts := make([]string, 0, len(v))
		for _, item := range v {
			body, err := jsJSONStringify(item)
			if err != nil {
				return "", err
			}
			parts = append(parts, body)
		}
		return "[" + strings.Join(parts, ",") + "]", nil
	case []string:
		parts := make([]string, 0, len(v))
		for _, item := range v {
			body, err := jsJSONStringify(item)
			if err != nil {
				return "", err
			}
			parts = append(parts, body)
		}
		return "[" + strings.Join(parts, ",") + "]", nil
	case map[string]any:
		keys := keysOfMap(v)
		parts := make([]string, 0, len(keys))
		for _, key := range keys {
			if isInternalMetaKey(key) {
				continue
			}
			body, err := jsJSONStringify(v[key])
			if err != nil {
				return "", err
			}
			name, err := json.Marshal(key)
			if err != nil {
				return "", err
			}
			parts = append(parts, string(name)+":"+body)
		}
		return "{" + strings.Join(parts, ",") + "}", nil
	default:
		body, err := json.Marshal(v)
		return string(body), err
	}
}

func stringSliceToAny(values []string) []any {
	out := make([]any, 0, len(values))
	for _, item := range values {
		item = strings.TrimSpace(item)
		if item == "" {
			continue
		}
		out = append(out, item)
	}
	return out
}

func splitScreenSum(sum int) (int, int) {
	common := [][2]int{
		{2048, 1152},
		{1920, 1080},
		{1536, 864},
		{1440, 900},
		{1600, 900},
		{1366, 768},
	}
	for _, item := range common {
		if item[0]+item[1] == sum {
			return item[0], item[1]
		}
	}
	if sum > 2000 {
		width := int(math.Round(float64(sum) * 0.64))
		height := sum - width
		return width, height
	}
	return sum, 0
}

func jsonString(value any) string {
	if value == nil {
		return ""
	}
	if text, ok := value.(string); ok {
		return text
	}
	return fmt.Sprintf("%v", value)
}

func jsonFloat(value any) float64 {
	switch v := value.(type) {
	case float64:
		return v
	case int:
		return float64(v)
	case int64:
		return float64(v)
	case string:
		parsed, err := strconv.ParseFloat(strings.TrimSpace(v), 64)
		if err == nil {
			return parsed
		}
	}
	return 0
}

func regKey(value any) string {
	switch v := value.(type) {
	case nil:
		return "nil"
	case string:
		return "s:" + v
	case int:
		return "n:" + strconv.Itoa(v)
	case int64:
		return "n:" + strconv.FormatInt(v, 10)
	case float64:
		if math.Trunc(v) == v {
			return "n:" + strconv.FormatInt(int64(v), 10)
		}
		return "n:" + strconv.FormatFloat(v, 'g', -1, 64)
	default:
		return "x:" + fmt.Sprintf("%v", value)
	}
}

func toIntIndex(value any) int {
	switch v := value.(type) {
	case int:
		return v
	case int64:
		return int(v)
	case float64:
		if math.Trunc(v) == v {
			return int(v)
		}
	case string:
		if parsed, err := strconv.Atoi(strings.TrimSpace(v)); err == nil {
			return parsed
		}
	}
	return -1
}

func copyAnySlice(value []any) []any {
	if len(value) == 0 {
		return []any{}
	}
	out := make([]any, len(value))
	copy(out, value)
	return out
}

func latin1Base64Decode(value string) (string, error) {
	body, err := base64.StdEncoding.DecodeString(value)
	if err != nil {
		return "", err
	}
	return string(bytesToLatin1Runes(body)), nil
}

func latin1Base64Encode(value string) string {
	return base64.StdEncoding.EncodeToString(latin1StringToBytes(value))
}

func xorString(data, key string) string {
	if key == "" {
		return data
	}
	dataBytes := latin1StringToBytes(data)
	keyBytes := latin1StringToBytes(key)
	out := make([]byte, len(dataBytes))
	for idx := range dataBytes {
		out[idx] = dataBytes[idx] ^ keyBytes[idx%len(keyBytes)]
	}
	return string(bytesToLatin1Runes(out))
}

func latin1StringToBytes(value string) []byte {
	bytes := make([]byte, 0, len(value))
	for _, r := range value {
		bytes = append(bytes, byte(r))
	}
	return bytes
}

func bytesToLatin1Runes(value []byte) []rune {
	out := make([]rune, 0, len(value))
	for _, b := range value {
		out = append(out, rune(b))
	}
	return out
}

/*
LINUXDO：ius.
*/
