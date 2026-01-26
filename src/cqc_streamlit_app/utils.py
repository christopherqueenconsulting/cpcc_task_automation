#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import tempfile
import threading
import zipfile
from random import randint
from typing import TypeVar, cast, Union, Any, Tuple

import streamlit as st
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_openai import ChatOpenAI
from streamlit.delta_generator import DeltaGenerator
from streamlit.elements.lib.mutable_status_container import StatusContainer
from streamlit.errors import NoSessionContext
from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx, SCRIPT_RUN_CONTEXT_ATTR_NAME
from streamlit.runtime.uploaded_file_manager import UploadedFile

CODE_LANGUAGES = [
    "abap", "abnf", "actionscript", "ada", "agda", "al", "antlr4", "apacheconf",
    "apex", "apl", "applescript", "aql", "arduino", "arff", "asciidoc", "asm6502",
    "asmatmel", "aspnet", "autohotkey", "autoit", "avisynth", "avroIdl", "bash",
    "basic", "batch", "bbcode", "bicep", "birb", "bison", "bnf", "brainfuck",
    "brightscript", "bro", "bsl", "c", "cfscript", "chaiscript", "cil", "clike",
    "clojure", "cmake", "cobol", "coffeescript", "concurnas", "coq", "cpp", "crystal",
    "csharp", "cshtml", "csp", "cssExtras", "css", "csv", "cypher", "d", "dart",
    "dataweave", "dax", "dhall", "diff", "django", "dnsZoneFile", "docker", "dot",
    "ebnf", "editorconfig", "eiffel", "ejs", "elixir", "elm", "erb", "erlang",
    "etlua", "excelFormula", "factor", "falselang", "firestoreSecurityRules", "flow",
    "fortran", "fsharp", "ftl", "gap", "gcode", "gdscript", "gedcom", "gherkin",
    "git", "glsl", "gml", "gn", "goModule", "go", "graphql", "groovy", "haml",
    "handlebars", "haskell", "haxe", "hcl", "hlsl", "hoon", "hpkp", "hsts", "http",
    "ichigojam", "icon", "icuMessageFormat", "idris", "iecst", "ignore", "inform7",
    "ini", "io", "j", "java", "javadoc", "javadoclike", "javascript", "javastacktrace",
    "jexl", "jolie", "jq", "jsExtras", "jsTemplates", "jsdoc", "json", "json5", "jsonp",
    "jsstacktrace", "jsx", "julia", "keepalived", "keyman", "kotlin", "kumir", "kusto",
    "latex", "latte", "less", "lilypond", "liquid", "lisp", "livescript", "llvm", "log",
    "lolcode", "lua", "magma", "makefile", "markdown", "markupTemplating", "markup",
    "matlab", "maxscript", "mel", "mermaid", "mizar", "mongodb", "monkey", "moonscript",
    "n1ql", "n4js", "nand2tetrisHdl", "naniscript", "nasm", "neon", "nevod", "nginx",
    "nim", "nix", "nsis", "objectivec", "ocaml", "opencl", "openqasm", "oz", "parigp",
    "parser", "pascal", "pascaligo", "pcaxis", "peoplecode", "perl", "phpExtras", "php",
    "phpdoc", "plsql", "powerquery", "powershell", "processing", "prolog", "promql",
    "properties", "protobuf", "psl", "pug", "puppet", "pure", "purebasic", "purescript",
    "python", "q", "qml", "qore", "qsharp", "r", "racket", "reason", "regex", "rego",
    "renpy", "rest", "rip", "roboconf", "robotframework", "ruby", "rust", "sas", "sass",
    "scala", "scheme", "scss", "shellSession", "smali", "smalltalk", "smarty", "sml",
    "solidity", "solutionFile", "soy", "sparql", "splunkSpl", "sqf", "sql", "squirrel",
    "stan", "stylus", "swift", "systemd", "t4Cs", "t4Templating", "t4Vb", "tap", "tcl",
    "textile", "toml", "tremor", "tsx", "tt2", "turtle", "twig", "typescript", "typoscript",
    "unrealscript", "uorazor", "uri", "v", "vala", "vbnet", "velocity", "verilog", "vhdl",
    "vim", "visualBasic", "warpscript", "wasm", "webIdl", "wiki", "wolfram", "wren", "xeora",
    "xmlDoc", "xojo", "xquery", "yaml", "yang", "zig"
]

mime_types_str = """
.3dm	x-world/x-3dmf
.3dmf	x-world/x-3dmf
.7z	application/x-7z-compressed
.a	application/octet-stream
.aab	application/x-authorware-bin
.aam	application/x-authorware-map
.aas	application/x-authorware-seg
.abc	text/vnd.abc
.acgi	text/html
.afl	video/animaflex
.ai	application/postscript
.aif	audio/aiff
.aif	audio/x-aiff
.aifc	audio/aiff
.aifc	audio/x-aiff
.aiff	audio/aiff
.aiff	audio/x-aiff
.aim	application/x-aim
.aip	text/x-audiosoft-intra
.ani	application/x-navi-animation
.aos	application/x-nokia-9000-communicator-add-on-software
.aps	application/mime
.arc	application/octet-stream
.arj	application/arj
.arj	application/octet-stream
.art	image/x-jg
.asf	video/x-ms-asf
.asm	text/x-asm
.asp	text/asp
.asx	application/x-mplayer2
.asx	video/x-ms-asf
.asx	video/x-ms-asf-plugin
.au	audio/basic
.au	audio/x-au
.avi	application/x-troff-msvideo
.avi	video/avi
.avi	video/msvideo
.avi	video/x-msvideo
.avs	video/avs-video
.bcpio	application/x-bcpio
.bin	application/mac-binary
.bin	application/macbinary
.bin	application/octet-stream
.bin	application/x-binary
.bin	application/x-macbinary
.bm	image/bmp
.bmp	image/bmp
.bmp	image/x-windows-bmp
.boo	application/book
.book	application/book
.boz	application/x-bzip2
.bsh	application/x-bsh
.bz	application/x-bzip
.bz2	application/x-bzip2
.c	text/plain
.c	text/x-c
.c++	text/plain
.cat	application/vnd.ms-pki.seccat
.cc	text/plain
.cc	text/x-c
.ccad	application/clariscad
.cco	application/x-cocoa
.cdf	application/cdf
.cdf	application/x-cdf
.cdf	application/x-netcdf
.cer	application/pkix-cert
.cer	application/x-x509-ca-cert
.cha	application/x-chat
.chat	application/x-chat
.class	application/java
.class	application/java-byte-code
.class	application/x-java-class
.com	application/octet-stream
.com	text/plain
.conf	text/plain
.cpio	application/x-cpio
.cpp	text/x-c
.cpt	application/mac-compactpro
.cpt	application/x-compactpro
.cpt	application/x-cpt
.crl	application/pkcs-crl
.crl	application/pkix-crl
.crt	application/pkix-cert
.crt	application/x-x509-ca-cert
.crt	application/x-x509-user-cert
.csh	application/x-csh
.csh	text/x-script.csh
.css	application/x-pointplus
.css	text/css
.csv	text/csv
.cxx	text/plain
.dcr	application/x-director
.deepv	application/x-deepv
.def	text/plain
.der	application/x-x509-ca-cert
.dif	video/x-dv
.dir	application/x-director
.dl	video/dl
.dl	video/x-dl
.doc	application/msword
.docx	application/vnd.openxmlformats-officedocument.wordprocessingml.document
.dot	application/msword
.dp	application/commonground
.drw	application/drafting
.dump	application/octet-stream
.dv	video/x-dv
.dvi	application/x-dvi
.dwf	model/vnd.dwf
.dwg	application/acad
.dwg	image/vnd.dwg
.dwg	image/x-dwg
.dxf	application/dxf
.dxf	image/vnd.dwg
.dxf	image/x-dwg
.dxr	application/x-director
.el	text/x-script.elisp
.elc	application/x-elc
.env	application/x-envoy
.eot	application/vnd.ms-fontobject
.eps	application/postscript
.es	application/x-esrehber
.etx	text/x-setext
.evy	application/envoy
.evy	application/x-envoy
.exe	application/octet-stream
.f	text/plain
.f	text/x-fortran
.f77	text/x-fortran
.f90	text/plain
.f90	text/x-fortran
.fdf	application/vnd.fdf
.fif	application/fractals
.fif	image/fif
.flac	audio/flac
.fli	video/fli
.fli	video/x-fli
.flo	image/florian
.flx	text/vnd.fmi.flexstor
.fmf	video/x-atomic3d-feature
.for	text/plain
.for	text/x-fortran
.fpx	image/vnd.fpx
.fpx	image/vnd.net-fpx
.frl	application/freeloader
.funk	audio/make
.g	text/plain
.g3	image/g3fax
.gif	image/gif
.gl	video/gl
.gl	video/x-gl
.gsd	audio/x-gsm
.gsm	audio/x-gsm
.gsp	application/x-gsp
.gss	application/x-gss
.gtar	application/x-gtar
.gz	application/x-compressed
.gz	application/x-gzip
.gzip	application/x-gzip
.gzip	multipart/x-gzip
.h	text/plain
.h	text/x-h
.hdf	application/x-hdf
.help	application/x-helpfile
.hgl	application/vnd.hp-hpgl
.hh	text/plain
.hh	text/x-h
.hlb	text/x-script
.hlp	application/hlp
.hlp	application/x-helpfile
.hlp	application/x-winhelp
.hpg	application/vnd.hp-hpgl
.hpgl	application/vnd.hp-hpgl
.hqx	application/binhex
.hqx	application/binhex4
.hqx	application/mac-binhex
.hqx	application/mac-binhex40
.hqx	application/x-binhex40
.hqx	application/x-mac-binhex40
.hta	application/hta
.htc	text/x-component
.htm	text/html
.html	text/html
.htmls	text/html
.htt	text/webviewhtml
.htx	text/html
.ice	x-conference/x-cooltalk
.ico	image/x-icon
.ics	text/calendar
.idc	text/plain
.ief	image/ief
.iefs	image/ief
.iges	application/iges
.iges	model/iges
.igs	application/iges
.igs	model/iges
.ima	application/x-ima
.imap	application/x-httpd-imap
.inf	application/inf
.ins	application/x-internett-signup
.ip	application/x-ip2
.isu	video/x-isvideo
.it	audio/it
.iv	application/x-inventor
.ivr	i-world/i-vrml
.ivy	application/x-livescreen
.jam	audio/x-jam
.jav	text/plain
.jav	text/x-java-source
.java	text/plain
.java	text/x-java-source
.jcm	application/x-java-commerce
.jfif	image/jpeg
.jfif	image/pjpeg
.jfif-tbnl	image/jpeg
.jpe	image/jpeg
.jpe	image/pjpeg
.jpeg	image/jpeg
.jpeg	image/pjpeg
.jpg	image/jpeg
.jpg	image/pjpeg
.jps	image/x-jps
.js	application/x-javascript
.js	application/javascript
.js	application/ecmascript
.js	text/javascript
.js	text/ecmascript
.json	application/json
.jut	image/jutvision
.kar	audio/midi
.kar	music/x-karaoke
.ksh	application/x-ksh
.ksh	text/x-script.ksh
.la	audio/nspaudio
.la	audio/x-nspaudio
.lam	audio/x-liveaudio
.latex	application/x-latex
.lha	application/lha
.lha	application/octet-stream
.lha	application/x-lha
.lhx	application/octet-stream
.list	text/plain
.lma	audio/nspaudio
.lma	audio/x-nspaudio
.log	text/plain
.lsp	application/x-lisp
.lsp	text/x-script.lisp
.lst	text/plain
.lsx	text/x-la-asf
.ltx	application/x-latex
.lzh	application/octet-stream
.lzh	application/x-lzh
.lzx	application/lzx
.lzx	application/octet-stream
.lzx	application/x-lzx
.m	text/plain
.m	text/x-m
.m1v	video/mpeg
.m2a	audio/mpeg
.m2v	video/mpeg
.m3u	audio/x-mpequrl
.man	application/x-troff-man
.map	application/x-navimap
.mar	text/plain
.mbd	application/mbedlet
.mc$	application/x-magic-cap-package-1.0
.mcd	application/mcad
.mcd	application/x-mathcad
.mcf	image/vasa
.mcf	text/mcf
.mcp	application/netmc
.me	application/x-troff-me
.mht	message/rfc822
.mhtml	message/rfc822
.mid	application/x-midi
.mid	audio/midi
.mid	audio/x-mid
.mid	audio/x-midi
.mid	music/crescendo
.mid	x-music/x-midi
.midi	application/x-midi
.midi	audio/midi
.midi	audio/x-mid
.midi	audio/x-midi
.midi	music/crescendo
.midi	x-music/x-midi
.mif	application/x-frame
.mif	application/x-mif
.mime	message/rfc822
.mime	www/mime
.mjf	audio/x-vnd.audioexplosion.mjuicemediafile
.mjpg	video/x-motion-jpeg
.mka	audio/x-matroska
.mkv	video/x-matroska
.mm	application/base64
.mm	application/x-meme
.mme	application/base64
.mod	audio/mod
.mod	audio/x-mod
.moov	video/quicktime
.mov	video/quicktime
.movie	video/x-sgi-movie
.mp2	audio/mpeg
.mp2	audio/x-mpeg
.mp2	video/mpeg
.mp2	video/x-mpeg
.mp2	video/x-mpeq2a
.mp3	audio/mpeg3
.mp3	audio/x-mpeg-3
.mp3	video/mpeg
.mp3	video/x-mpeg
.mp4	video/mp4
.mpa	audio/mpeg
.mpa	video/mpeg
.mpc	application/x-project
.mpe	video/mpeg
.mpeg	video/mpeg
.mpg	audio/mpeg
.mpg	video/mpeg
.mpga	audio/mpeg
.mpp	application/vnd.ms-project
.mpt	application/x-project
.mpv	application/x-project
.mpx	application/x-project
.mrc	application/marc
.ms	application/x-troff-ms
.mv	video/x-sgi-movie
.my	audio/make
.mzz	application/x-vnd.audioexplosion.mzz
.nap	image/naplps
.naplps	image/naplps
.nc	application/x-netcdf
.ncm	application/vnd.nokia.configuration-message
.nif	image/x-niff
.niff	image/x-niff
.nix	application/x-mix-transfer
.nsc	application/x-conference
.nvd	application/x-navidoc
.o	application/octet-stream
.oda	application/oda
.ogg	audio/ogg
.ogg	video/ogg
.omc	application/x-omc
.omcd	application/x-omcdatamaker
.omcr	application/x-omcregerator
.otf	font/otf
.p	text/x-pascal
.p10	application/pkcs10
.p10	application/x-pkcs10
.p12	application/pkcs-12
.p12	application/x-pkcs12
.p7a	application/x-pkcs7-signature
.p7c	application/pkcs7-mime
.p7c	application/x-pkcs7-mime
.p7m	application/pkcs7-mime
.p7m	application/x-pkcs7-mime
.p7r	application/x-pkcs7-certreqresp
.p7s	application/pkcs7-signature
.part	application/pro_eng
.pas	text/pascal
.pbm	image/x-portable-bitmap
.pcl	application/vnd.hp-pcl
.pcl	application/x-pcl
.pct	image/x-pict
.pcx	image/x-pcx
.pdb	chemical/x-pdb
.pdf	application/pdf
.pfunk	audio/make
.pfunk	audio/make.my.funk
.pgm	image/x-portable-graymap
.pgm	image/x-portable-greymap
.pic	image/pict
.pict	image/pict
.pkg	application/x-newton-compatible-pkg
.pko	application/vnd.ms-pki.pko
.pl	text/plain
.pl	text/x-script.perl
.plx	application/x-pixclscript
.pm	image/x-xpixmap
.pm	text/x-script.perl-module
.pm4	application/x-pagemaker
.pm5	application/x-pagemaker
.png	image/png
.pnm	application/x-portable-anymap
.pnm	image/x-portable-anymap
.pot	application/mspowerpoint
.pot	application/vnd.ms-powerpoint
.pov	model/x-pov
.ppa	application/vnd.ms-powerpoint
.ppm	image/x-portable-pixmap
.pps	application/mspowerpoint
.pps	application/vnd.ms-powerpoint
.ppt	application/mspowerpoint
.ppt	application/powerpoint
.ppt	application/vnd.ms-powerpoint
.ppt	application/x-mspowerpoint
.pptx	application/vnd.openxmlformats-officedocument.presentationml.presentation
.ppz	application/mspowerpoint
.pre	application/x-freelance
.prt	application/pro_eng
.ps	application/postscript
.psd	application/octet-stream
.pvu	paleovu/x-pv
.pwz	application/vnd.ms-powerpoint
.py	text/x-script.phyton
.pyc	application/x-bytecode.python
.qcp	audio/vnd.qcelp
.qd3	x-world/x-3dmf
.qd3d	x-world/x-3dmf
.qif	image/x-quicktime
.qt	video/quicktime
.qtc	video/x-qtc
.qti	image/x-quicktime
.qtif	image/x-quicktime
.ra	audio/x-pn-realaudio
.ra	audio/x-pn-realaudio-plugin
.ra	audio/x-realaudio
.ram	audio/x-pn-realaudio
.ras	application/x-cmu-raster
.ras	image/cmu-raster
.ras	image/x-cmu-raster
.rast	image/cmu-raster
.rar	application/vnd.rar
.rexx	text/x-script.rexx
.rf	image/vnd.rn-realflash
.rgb	image/x-rgb
.rm	application/vnd.rn-realmedia
.rm	audio/x-pn-realaudio
.rmi	audio/mid
.rmm	audio/x-pn-realaudio
.rmp	audio/x-pn-realaudio
.rmp	audio/x-pn-realaudio-plugin
.rng	application/ringing-tones
.rng	application/vnd.nokia.ringing-tone
.rnx	application/vnd.rn-realplayer
.roff	application/x-troff
.rp	image/vnd.rn-realpix
.rpm	audio/x-pn-realaudio-plugin
.rt	text/richtext
.rt	text/vnd.rn-realtext
.rtf	application/rtf
.rtf	application/x-rtf
.rtf	text/richtext
.rtx	application/rtf
.rtx	text/richtext
.rv	video/vnd.rn-realvideo
.s	text/x-asm
.s3m	audio/s3m
.saveme	application/octet-stream
.sbk	application/x-tbook
.scm	application/x-lotusscreencam
.scm	text/x-script.guile
.scm	text/x-script.scheme
.scm	video/x-scm
.sdml	text/plain
.sdp	application/sdp
.sdp	application/x-sdp
.sdr	application/sounder
.sea	application/sea
.sea	application/x-sea
.set	application/set
.sgm	text/sgml
.sgm	text/x-sgml
.sgml	text/sgml
.sgml	text/x-sgml
.sh	application/x-bsh
.sh	application/x-sh
.sh	application/x-shar
.sh	text/x-script.sh
.shar	application/x-bsh
.shar	application/x-shar
.shtml	text/html
.shtml	text/x-server-parsed-html
.sid	audio/x-psid
.sit	application/x-sit
.sit	application/x-stuffit
.skd	application/x-koan
.skm	application/x-koan
.skp	application/x-koan
.skt	application/x-koan
.sl	application/x-seelogo
.smi	application/smil
.smil	application/smil
.snd	audio/basic
.snd	audio/x-adpcm
.sol	application/solids
.spc	application/x-pkcs7-certificates
.spc	text/x-speech
.spl	application/futuresplash
.spr	application/x-sprite
.sprite	application/x-sprite
.src	application/x-wais-source
.ssi	text/x-server-parsed-html
.ssm	application/streamingmedia
.sst	application/vnd.ms-pki.certstore
.step	application/step
.stl	application/sla
.stl	application/vnd.ms-pki.stl
.stl	application/x-navistyle
.stp	application/step
.sv4cpio	application/x-sv4cpio
.sv4crc	application/x-sv4crc
.svf	image/vnd.dwg
.svf	image/x-dwg
.svg	image/svg+xml
.svr	application/x-world
.svr	x-world/x-svr
.swf	application/x-shockwave-flash
.t	application/x-troff
.talk	text/x-speech
.tar	application/x-tar
.tbk	application/toolbook
.tbk	application/x-tbook
.tcl	application/x-tcl
.tcl	text/x-script.tcl
.tcsh	text/x-script.tcsh
.tex	application/x-tex
.texi	application/x-texinfo
.texinfo	application/x-texinfo
.text	application/plain
.text	text/plain
.tgz	application/gnutar
.tgz	application/x-compressed
.tif	image/tiff
.tif	image/x-tiff
.tiff	image/tiff
.tiff	image/x-tiff
.tr	application/x-troff
.ts	video/mp2t
.tsi	audio/tsp-audio
.tsp	application/dsptype
.tsp	audio/tsplayer
.tsv	text/tab-separated-values
.turbot	image/florian
.txt	text/plain
.uil	text/x-uil
.uni	text/uri-list
.unis	text/uri-list
.unv	application/i-deas
.uri	text/uri-list
.uris	text/uri-list
.ustar	application/x-ustar
.ustar	multipart/x-ustar
.uu	application/octet-stream
.uu	text/x-uuencode
.uue	text/x-uuencode
.vcd	application/x-cdlink
.vcs	text/x-vcalendar
.vda	application/vda
.vdo	video/vdo
.vew	application/groupwise
.viv	video/vivo
.viv	video/vnd.vivo
.vivo	video/vivo
.vivo	video/vnd.vivo
.vmd	application/vocaltec-media-desc
.vmf	application/vocaltec-media-file
.voc	audio/voc
.voc	audio/x-voc
.vos	video/vosaic
.vox	audio/voxware
.vqe	audio/x-twinvq-plugin
.vqf	audio/x-twinvq
.vql	audio/x-twinvq-plugin
.vrml	application/x-vrml
.vrml	model/vrml
.vrml	x-world/x-vrml
.vrt	x-world/x-vrt
.vsd	application/x-visio
.vst	application/x-visio
.vsw	application/x-visio
.w60	application/wordperfect6.0
.w61	application/wordperfect6.1
.w6w	application/msword
.wav	audio/wav
.wav	audio/x-wav
.wb1	application/x-qpro
.wbmp	image/vnd.wap.wbmp
.web	application/vnd.xara
.webm	video/webm
.webp	image/webp
.wiz	application/msword
.wk1	application/x-123
.wmf	windows/metafile
.wml	text/vnd.wap.wml
.wmlc	application/vnd.wap.wmlc
.wmls	text/vnd.wap.wmlscript
.wmlsc	application/vnd.wap.wmlscriptc
.word	application/msword
.woff	font/woff
.woff2	font/woff2
.wp	application/wordperfect
.wp5	application/wordperfect
.wp5	application/wordperfect6.0
.wp6	application/wordperfect
.wpd	application/wordperfect
.wpd	application/x-wpwin
.wq1	application/x-lotus
.wri	application/mswrite
.wri	application/x-wri
.wrl	application/x-world
.wrl	model/vrml
.wrl	x-world/x-vrml
.wrz	model/vrml
.wrz	x-world/x-vrml
.wsc	text/scriplet
.wsrc	application/x-wais-source
.wtk	application/x-wintalk
.xbm	image/x-xbitmap
.xbm	image/x-xbm
.xbm	image/xbm
.xdr	video/x-amt-demorun
.xgz	xgl/drawing
.xif	image/vnd.xiff
.xl     application/excel
.xla	application/excel
.xla	application/x-excel
.xla	application/x-msexcel
.xlb	application/excel
.xlb	application/vnd.ms-excel
.xlb	application/x-excel
.xlc	application/excel
.xlc	application/vnd.ms-excel
.xlc	application/x-excel
.xld	application/excel
.xld	application/x-excel
.xlk	application/excel
.xlk	application/x-excel
.xll	application/excel
.xll	application/vnd.ms-excel
.xll	application/x-excel
.xlm	application/excel
.xlm	application/vnd.ms-excel
.xlm	application/x-excel
.xls	application/excel
.xls	application/vnd.ms-excel
.xls	application/x-excel
.xls	application/x-msexcel
.xlt	application/excel
.xlt	application/x-excel
.xlv	application/excel
.xlv	application/x-excel
.xlw	application/excel
.xlw	application/vnd.ms-excel
.xlw	application/x-excel
.xlw	application/x-msexcel
.xm	audio/xm
.xml	application/xml
.xml	text/xml
.xmz	xgl/movie
.xpix	application/x-vnd.ls-xpix
.xpm	image/x-xpixmap
.xpm	image/xpm
.x-png	image/png
.xlsx	application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
.xsr	video/x-amt-showrun
.xwd	image/x-xwd
.xwd	image/x-xwindowdump
.xyz	chemical/x-pdb
.yaml	application/x-yaml
.yml	application/x-yaml
.z	application/x-compress
.z	application/x-compressed
.zip	application/x-compressed
.zip	application/x-zip-compressed
.zip	application/zip
.zip	multipart/x-zip
.zoo	application/octet-stream
.zsh	text/x-script.zsh
"""


@st.cache_data
def get_cpcc_css():
    # Embed custom fonts using HTML and CSS
    css = """
        <style>
            @font-face {
                font-family: "Franklin Gothic";
                src: url("https://db.onlinewebfonts.com/t/9c9dbb999dd7068f51335d93cc7328bd.eot");
                src: url("https://db.onlinewebfonts.com/t/9c9dbb999dd7068f51335d93cc7328bd.eot?#iefix")format("embedded-opentype"),
                url("https://db.onlinewebfonts.com/t/9c9dbb999dd7068f51335d93cc7328bd.woff2")format("woff2"),
                url("https://db.onlinewebfonts.com/t/9c9dbb999dd7068f51335d93cc7328bd.woff")format("woff"),
                url("https://db.onlinewebfonts.com/t/9c9dbb999dd7068f51335d93cc7328bd.ttf")format("truetype"),
                url("https://db.onlinewebfonts.com/t/9c9dbb999dd7068f51335d93cc7328bd.svg#Franklin Gothic")format("svg");
            }

            @font-face {
                font-family: 'ITC New Baskerville';
                src: url("https://db.onlinewebfonts.com/t/501ade6e29baa5c62c15ec28f3ed2c62.eot");
                src: url("https://db.onlinewebfonts.com/t/501ade6e29baa5c62c15ec28f3ed2c62.eot?#iefix")format("embedded-opentype"),
                url("https://db.onlinewebfonts.com/t/501ade6e29baa5c62c15ec28f3ed2c62.woff2")format("woff2"),
                url("https://db.onlinewebfonts.com/t/501ade6e29baa5c62c15ec28f3ed2c62.woff")format("woff"),
                url("https://db.onlinewebfonts.com/t/501ade6e29baa5c62c15ec28f3ed2c62.ttf")format("truetype"),
                url("https://db.onlinewebfonts.com/t/501ade6e29baa5c62c15ec28f3ed2c62.svg#ITC New Baskerville")format("svg");
            }

            body {
                font-family: 'Franklin Gothic', sans-serif;
            }

            h1, h2, h3, h4, h5, h6 {
                font-family: 'Franklin Gothic', sans-serif;
                font-weight: normal;
            }

            p {
                font-family: 'ITC New Baskerville', sans-serif;
                font-weight: normal;
            }
        </style>
        """
    return css


@st.cache_resource(hash_funcs={ChatOpenAI: id})
def get_custom_llm(temperature: float, model: str, service_tier: str ="default") -> ChatOpenAI:
    """
    This function returns a cached instance of ChatOpenAI based on the temperature and model.
    If the temperature or model changes, a new instance will be created and cached.
    """
    return ChatOpenAI(temperature=temperature,
                      model=model,
                      openai_api_key=st.session_state.openai_api_key,
                      use_responses_api=True,
                      service_tier=service_tier
                      # streaming=True
                      )


def get_file_extension_from_filepath(file_path: str, remove_leading_dot: bool = False) -> str:
    basename = os.path.basename(file_path)
    file_name, file_extension = os.path.splitext(basename)
    if remove_leading_dot and file_extension.startswith("."):
        # st.info("Removing leading dot from file extension: " + file_extension)
        file_extension = file_extension[1:]

    if file_extension:
        file_extension = file_extension.lower()

    # st.info("Base Name: " + basename + " | File Name: " + file_name + " | File Extension: " + file_extension)

    return file_extension


def get_language_from_file_path(file_path):
    # Extract file extension from the file path
    file_extension = get_file_extension_from_filepath(file_path, True)

    # Check if the file extension exists in the mapping
    if file_extension in CODE_LANGUAGES:
        # st.info(file_extension + " | Found in CODE_LANGUAGES")
        return file_extension
    else:
        # st.info(file_extension + " | NOT Found in CODE_LANGUAGES")
        return None  # Return None if the file extension is not found


def define_code_language_selection(unique_key: str | int, default_option: str = 'java'):
    # List of available languages

    selected_language = st.selectbox(label="Select Code Language",
                                     key="language_select_" + unique_key,
                                     options=CODE_LANGUAGES,
                                     index=CODE_LANGUAGES.index(default_option))
    return selected_language


# streamlit model/tier selector with updated GPT-5 Flex pricing (per 1M tokens)
from typing import Any, Dict, Optional
import os
import json
import streamlit as st


def define_chatGPTModel(unique_key: str | int,
                        default_min_value: float = 0.2,
                        default_max_value: float = 0.8,
                        default_temp_value: float = 0.2,
                        default_step: float = 0.1,
                        default_option: str = "gpt-5") -> Dict[str, Any]:
    """
    Presents model selection, temperature slider, and service tier.
    Returns JSON-serializable dict:
      {
        "model": str,
        "temperature": float,
        "service_tier": "Standard" | "Priority" | "Flex",
        "pricing": {input, cached, output, unit},
        "token_limits": {"context_window": int, "max_input": int|None, "max_output": int|None}
      }

    Notes
    - Units are per **1M tokens** (matches OpenAI pricing pages).
    - Models listed support structured outputs (JSON/JSON Schema via Responses API).
    """

    uk = str(unique_key)

    # === Models supporting structured output ===
    model_options = [
        # GPT-5 family only (standardized)
        "gpt-5",
        "gpt-5-mini",
        "gpt-5-nano",
    ]
    if default_option not in model_options:
        default_option = "gpt-5"

    # === Indicative context windows ===
    token_limits = {
        # GPT-5 family (API page lists 400K total; 128K max output cap)
        "gpt-5": {"context_window": 400_000, "max_input": 272_000, "max_output": 128_000},
        "gpt-5-mini": {"context_window": 400_000, "max_input": 272_000, "max_output": 128_000},
        "gpt-5-nano": {"context_window": 400_000, "max_input": 272_000, "max_output": 128_000},
    }

    # === Standard pricing (per 1M tokens) ===
    # Source: OpenAI API Pricing page
    standard_prices = {
        "gpt-5": {"input": 1.25, "cached": 0.125, "output": 10.00, "unit": "1M tokens"},
        "gpt-5-mini": {"input": 0.25, "cached": 0.025, "output": 2.00, "unit": "1M tokens"},
        "gpt-5-nano": {"input": 0.05, "cached": 0.005, "output": 0.40, "unit": "1M tokens"},
    }

    # === Priority pricing (per 1M tokens) ===
    # Source: OpenAI "Priority Processing for API Customers"
    priority_prices = {
        "gpt-5": {"input": 2.50, "cached": 0.250, "output": 20.00, "unit": "1M tokens"},
        "gpt-5-mini": {"input": 0.45, "cached": 0.045, "output": 3.60, "unit": "1M tokens"},
    }

    # === Flex pricing (per 1M tokens) — updated from your screenshot ===
    # If you want to override via env, set FLEX_PRICE_OVERRIDES as JSON.
    flex_prices_default = {
        "gpt-5": {"input": 0.625, "cached": 0.0625, "output": 5.00, "unit": "1M tokens"},
        "gpt-5-mini": {"input": 0.125, "cached": 0.0125, "output": 1.00, "unit": "1M tokens"},
        "gpt-5-nano": {"input": 0.025, "cached": 0.0025, "output": 0.20, "unit": "1M tokens"},
        # Leaving 4.x Flex out unless you explicitly want them; easy to add later.
    }
    flex_overrides_env = os.getenv("FLEX_PRICE_OVERRIDES")
    if flex_overrides_env:
        try:
            parsed = json.loads(flex_overrides_env)
            for k, v in parsed.items():
                if isinstance(v, dict):
                    flex_prices_default[k] = {**v, "unit": "1M tokens"}
        except Exception:
            pass

    def get_flex_price(model: str) -> Dict[str, Optional[float]]:
        if model in flex_prices_default:
            d = flex_prices_default[model]
            return {
                "input": d.get("input"),
                "cached": d.get("cached"),
                "output": d.get("output"),
                "unit": d.get("unit", "1M tokens"),
            }
        return {"input": None, "cached": None, "output": None, "unit": "1M tokens"}

    # === UI controls ===
    selected_model = st.selectbox(
        label="Select Model (structured output capable)",
        key=f"chat_select_{uk}",
        options=model_options,
        index=model_options.index(default_option)
    )

    service_tier = st.radio(
        label="Service Tier",
        key=f"chat_tier_{uk}",
        options=["Standard", "Priority", "Flex"],
        index=0
    )

    temperature = st.slider(
        label="Temperature",
        key=f"chat_temp_{uk}",
        min_value=max(default_min_value, 0.0),
        max_value=min(default_max_value, 1.0),
        step=default_step,
        value=default_temp_value,
        format="%.2f"
    )
    if temperature <= 0.3:
        st.caption("Low: most deterministic, best for strict JSON/schema.")
    elif temperature <= 0.7:
        st.caption("Medium: balanced creativity vs. schema adherence.")
    else:
        st.caption("High: diverse outputs; may reduce schema adherence.")

    # === Resolve pricing based on tier ===
    if service_tier == "Standard":
        pricing = standard_prices.get(selected_model,
                                      {"input": None, "cached": None, "output": None, "unit": "1M tokens"})
    elif service_tier == "Priority":
        pricing = priority_prices.get(selected_model,
                                      {"input": None, "cached": None, "output": None, "unit": "1M tokens"})
    else:  # Flex
        pricing = get_flex_price(selected_model)

    # === Display price + limits ===
    tl = token_limits.get(selected_model, {})
    cw = tl.get("context_window")
    max_in = tl.get("max_input")
    max_out = tl.get("max_output")

    def _fmt_price(p: Optional[float], label: str) -> Optional[str]:
        return f"{label}: ${p:.4f} / {pricing['unit']}" if isinstance(p, (int, float)) else None

    parts = [
        _fmt_price(pricing.get("input"), "Input"),
        _fmt_price(pricing.get("cached"), "Cached input"),
        _fmt_price(pricing.get("output"), "Output"),
    ]
    price_line = " | ".join([p for p in parts if p]) if any(parts) else "Pricing: not available"

    cw_bits = [f"Context window: ~{cw:,} tokens" if cw else "Context window: see model docs"]
    if max_in:
        cw_bits.append(f"Max input: ~{max_in:,}")
    if max_out:
        cw_bits.append(f"Max output: ~{max_out:,}")
    st.info(f"Model: {selected_model} | Tier: {service_tier} | {price_line} | {' | '.join(cw_bits)}")

    # === Optional: inline cost estimator ===
    with st.expander("Estimate cost for this request (optional)"):
        in_tokens = st.number_input("Estimated input tokens (prompt)", min_value=0, value=0, step=1000,
                                    key=f"in_tokens_{uk}")
        cached_ratio = st.slider("Estimated % of input tokens served from prompt cache", 0, 100, 0, 5,
                                 key=f"cached_ratio_{uk}")
        out_tokens = st.number_input("Estimated output tokens (completion)", min_value=0, value=0, step=1000,
                                     key=f"out_tokens_{uk}")

        def estimate_cost(pr: Dict[str, Any], in_tok: int, cached_pct: int, out_tok: int) -> Optional[float]:
            if pr.get("input") is None or pr.get("output") is None:
                return None
            cached = pr.get("cached")
            cached_tokens = int(in_tok * (cached_pct / 100.0))
            regular_tokens = max(0, in_tok - cached_tokens)
            per_m = 1_000_000.0
            input_cost = (regular_tokens / per_m) * pr["input"]
            cached_cost = (cached_tokens / per_m) * (cached if cached is not None else pr["input"])
            output_cost = (out_tok / per_m) * pr["output"]
            return round(input_cost + cached_cost + output_cost, 6)

        est = estimate_cost(pricing, in_tokens, cached_ratio, out_tokens)
        if est is None:
            st.warning("Pricing not available for this tier/model combination.")
        else:
            st.success(f"Estimated cost: ${est:,.6f}")

    # Map UI service tiers to LangChain BaseChatOpenAI service_tier values
    _service_tier_map = {"Standard": "default", "Priority": "auto", "Flex": "flex"}

    # ... inside define_chatGPTModel, after service_tier is set:
    langchain_service_tier = _service_tier_map.get(service_tier, "default")

    return {
        "model": selected_model,
        "temperature": float(temperature),
        "service_tier": service_tier,  # UI-facing
        "langchain_service_tier": langchain_service_tier,  # use this when creating ChatOpenAI(...)
        "pricing": pricing,
        "token_limits": {"context_window": cw, "max_input": max_in, "max_output": max_out},
    }


def reset_session_key_value(key: str):
    st.session_state[key] = str(randint(1000, 100000000))


# Type alias for return type
UploadedFileResult = Union[list[tuple[Any, str]], tuple[Any, str], tuple[None, None]]


def add_upload_file_element(
    uploader_text: str, 
    accepted_file_types: list[str], 
    success_message: bool = True,
    accept_multiple_files: bool = False, 
    key_prefix: str = ""
) -> UploadedFileResult:
    """Add a file uploader element with unique key generation.
    
    Args:
        uploader_text: Label for the file uploader
        accepted_file_types: List of accepted file extensions
        success_message: Whether to show success message on upload
        accept_multiple_files: Whether to accept multiple files
        key_prefix: Prefix for widget keys to ensure uniqueness across contexts
        
    Returns:
        If accept_multiple_files=True: List of (original_name, temp_path) tuples
        If accept_multiple_files=False: Single (original_name, temp_path) tuple
        If no files uploaded: (None, None)
    """
    # Button to reset the multi file uploader
    reset_label = "Reset " + uploader_text + " File Uploader"
    reset_key = key_prefix + reset_label.replace(" ", "_")

    if reset_key not in st.session_state:
        reset_session_key_value(reset_key)

    # Create compound widget key using both context-specific prefix and random value
    # This ensures global uniqueness even if random numbers collide across contexts
    widget_key = f"{reset_key}_{st.session_state[reset_key]}"
    
    uploaded_files = st.file_uploader(label=uploader_text, type=accepted_file_types,
                                      accept_multiple_files=accept_multiple_files, key=widget_key)

    if accept_multiple_files:
        if st.button("Remove All Files", key="Checkbox_" + widget_key):
            reset_session_key_value(reset_key)
            st.rerun()

        uploaded_file_paths = []
        for uploaded_file in uploaded_files:
            if uploaded_file is not None:
                # Get the original file name
                original_file_name = uploaded_file.name

                # Create a temporary file to store the uploaded file
                temp_file_name = upload_file_to_temp_path(uploaded_file)

                uploaded_file_paths.append((original_file_name, temp_file_name))
        if uploaded_files and success_message:
            st.success("File(s) uploaded successfully.")
        return uploaded_file_paths

    elif uploaded_files is not None:
        # Get the original file name
        original_file_name = uploaded_files.name
        # Create a temporary file to store the uploaded file
        temp_file_name = upload_file_to_temp_path(uploaded_files)

        if success_message:
            st.success("File uploaded successfully.")
        return original_file_name, temp_file_name
    else:
        return None, None


def upload_file_to_temp_path(uploaded_file: UploadedFile):
    file_extension = get_file_extension_from_filepath(uploaded_file.name)

    # Create a temporary file to store the uploaded instructions
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=file_extension)
    temp_file.write(uploaded_file.getvalue())
    # temp_file.close()

    return temp_file.name


def process_file(file_path, allowed_file_extensions):
    """ Using a file path determine if the file is a zip or single file and gives the contents back if single or dict mapping the studnet name and timestamp back to the combined contents"""

    # If it's a zip file
    if file_path.endswith('.zip'):
        with zipfile.ZipFile(file_path, 'r') as zip_file:
            folder_contents = {}
            for zip_info in zip_file.infolist():
                if any(zip_info.filename.lower().endswith(ext) for ext in allowed_file_extensions):
                    folder_path = os.path.dirname(zip_info.filename)
                    with zip_file.open(zip_info) as file:
                        file_contents = file.read()
                    folder_contents.setdefault(folder_path, []).append(file_contents)

            for folder_path, files in folder_contents.items():
                concatenated_contents = b''.join(files)
                print(f"Contents of folder '{folder_path}': {concatenated_contents.decode()}")

    # If it's a single file
    else:
        if any(file_path.lower().endswith(ext) for ext in allowed_file_extensions):
            with open(file_path, 'r') as file:
                print("Contents of single file:", file.read())


def choose_preferred_mime(mime_list):
    # Define a priority order for MIME types
    priority_order = [
        "application/octet-stream",
        "application/zip"

    ]

    for mime in priority_order:
        if mime in mime_list:
            return mime

    # Return the first MIME type if none match the priority order
    return mime_list[0]


def get_file_mime_type(file_extension: str):
    # Check if file_extension is prefixed with "." if not add it first
    if not file_extension.startswith("."):
        file_extension = "." + file_extension

    # Define the mapping of file extensions to MIME types
    mime_dict = {}
    lines = mime_types_str.strip().split('\n')
    for line in lines:
        try:
            key, value = line.split()
        except ValueError:
            print("Error splitting line: %s" % line)
            key = None
            value = None

        if key in mime_dict:
            mime_dict[key].append(value)
        else:
            mime_dict[key] = [value]

    # Create a dictionary with preferred MIME types
    preferred_mime_dict = {ext: choose_preferred_mime(mimes) for ext, mimes in mime_dict.items()}

    return preferred_mime_dict.get(file_extension, "application/octet-stream")


def on_download_click(download_button_placeholder: DeltaGenerator, file_path: str, button_label: str,
                      download_file_name: str):
    file_extension = get_file_extension_from_filepath(download_file_name)
    mime_type = get_file_mime_type(file_extension)
    # st.info("file_extension: " + file_extension + " | mime_type: " + mime_type)

    # file_content = read_file(file_path)
    # Read the content of the file
    with open(file_path, "rb") as file:
        file_content = file.read()

    # st.info("file_path: "+file_path+" | download_file_name: "+download_file_name)
    # st.markdown(file_content)

    # Trigger the download of the file
    download_button_placeholder.download_button(label=button_label, data=file_content,
                                                file_name=download_file_name, mime=mime_type
                                                # , key=download_file_name
                                                )


def create_zip_file(file_paths: list[tuple[str, str]]) -> str:
    # Create a temporary file to store the zip file
    zip_file = tempfile.NamedTemporaryFile(delete=False)
    zip_file.close()  # Close the file to use it as the output path for the zip file

    with zipfile.ZipFile(zip_file.name, 'w') as zipf:
        for orig_file_path, temp_file_path in file_paths:
            # Get the base file name from the original file path
            base_file_name = os.path.basename(orig_file_path)
            # Add the temporary file to the zip file with the original file name
            zipf.write(temp_file_path, arcname=base_file_name)

    # Return the path of the zip file
    return zip_file.name


def prefix_content_file_name(filename: str, content: str):
    return "# File: " + filename + "\n\n" + content


T = TypeVar("T")


def with_streamlit_context(fn: T) -> T:
    """Fix bug in streamlit which raises streamlit.errors.NoSessionContext."""
    ctx = get_script_run_ctx()

    if ctx is None:
        # Allow usage outside Streamlit (tests, CLI scripts) without raising.
        def _cb(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)
        return cast(T, _cb)

    def _cb(*args: Any, **kwargs: Any) -> Any:
        """Do it."""

        thread = threading.current_thread()
        do_nothing = hasattr(thread, SCRIPT_RUN_CONTEXT_ATTR_NAME) and (
                getattr(thread, SCRIPT_RUN_CONTEXT_ATTR_NAME) == ctx
        )

        if not do_nothing:
            setattr(thread, SCRIPT_RUN_CONTEXT_ATTR_NAME, ctx)

        # Call the callback.
        ret = fn(*args, **kwargs)

        if not do_nothing:
            # Why delattr? Because tasks for different users may be done by
            # the same thread at different times. Danger danger.
            delattr(thread, SCRIPT_RUN_CONTEXT_ATTR_NAME)
        return ret

    return cast(T, _cb)


class ChatGPTStatusCallbackHandler(BaseCallbackHandler):

    def __init__(
            self,
            status_container: StatusContainer,
            prefix_label: str = None
    ):
        self._status_container = status_container
        if prefix_label is None:
            self._prefix_label = ""
        else:
            self._prefix_label = prefix_label + " | "

    @with_streamlit_context
    def on_llm_start(
            self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any
    ) -> None:
        self._status_container.update(label=self._prefix_label + "Getting response from ChatGPT")

    @with_streamlit_context
    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        # self._status_container.update(label=self._prefix_label + "From ChatGPT: " + str(response))
        self._status_container.update(label=self._prefix_label + "ChatGPT Finished")

    @with_streamlit_context
    def on_llm_error(self, error: BaseException, *args: Any, **kwargs: Any) -> None:
        self._status_container.update(label=self._prefix_label + "ChatGPT Error: " + str(error))


def render_openai_debug_panel(
    correlation_id: str | None = None,
    error: Exception | None = None,
) -> None:
    """Render OpenAI debug panel in Streamlit UI when debug mode is enabled.
    
    Shows request/response details, correlation ID, and decision notes for
    troubleshooting OpenAI API calls.
    
    Args:
        correlation_id: Correlation ID for the request (if available)
        error: Exception that occurred (if any)
    """
    from cqc_cpcc.utilities.env_constants import CQC_OPENAI_DEBUG
    from cqc_cpcc.utilities.AI.openai_debug import get_debug_context
    from cqc_cpcc.utilities.AI.openai_exceptions import (
        OpenAISchemaValidationError,
        OpenAITransportError,
    )
    import json
    
    # Only show debug panel if debug mode is enabled
    if not CQC_OPENAI_DEBUG:
        return
    
    # Create collapsible debug panel
    with st.expander("🔍 OpenAI Debug Information", expanded=False):
        st.markdown("**Debug Mode Enabled** - This panel shows OpenAI request/response details.")
        
        # Show correlation ID
        if correlation_id:
            st.code(f"Correlation ID: {correlation_id}", language="text")
        else:
            st.warning("No correlation ID available (debug mode may have been off during request)")
        
        # Show error details if present
        if error:
            st.error("**Error Occurred:**")
            
            if isinstance(error, OpenAISchemaValidationError):
                st.markdown(f"**Type:** Schema Validation Error")
                st.markdown(f"**Schema:** {error.schema_name}")
                if error.decision_notes:
                    st.markdown(f"**Decision Notes:** {error.decision_notes}")
                if error.validation_errors:
                    st.markdown(f"**Validation Errors:** {len(error.validation_errors)}")
                    with st.expander("Show Validation Errors"):
                        st.json(error.validation_errors)
                if error.raw_output:
                    with st.expander("Show Raw Output"):
                        st.code(error.raw_output[:1000], language="json")  # Truncate to 1000 chars
            
            elif isinstance(error, OpenAITransportError):
                st.markdown(f"**Type:** Transport Error")
                if error.status_code:
                    st.markdown(f"**Status Code:** {error.status_code}")
                if error.retry_after:
                    st.markdown(f"**Retry After:** {error.retry_after}s")
            
            else:
                st.markdown(f"**Type:** {type(error).__name__}")
                st.markdown(f"**Message:** {str(error)}")
        
        # Load and show debug context from files
        if correlation_id:
            debug_context = get_debug_context(correlation_id)
            
            if debug_context:
                # Show request details
                if "request" in debug_context:
                    with st.expander("📤 Request Details"):
                        req = debug_context["request"]
                        st.markdown(f"**Model:** {req.get('model')}")
                        st.markdown(f"**Schema:** {req.get('schema_name')}")
                        st.markdown(f"**Timestamp:** {req.get('timestamp')}")
                        
                        # Show messages (prompts)
                        if "request" in req and "messages" in req["request"]:
                            st.markdown("**Messages:**")
                            for msg in req["request"]["messages"]:
                                role = msg.get("role", "unknown")
                                content = msg.get("content", "")
                                st.text_area(
                                    f"Message ({role})",
                                    content[:500],  # Truncate to 500 chars
                                    height=150,
                                    key=f"msg_{role}_{correlation_id}"
                                )
                        
                        # Download request JSON
                        request_json = json.dumps(req, indent=2)
                        st.download_button(
                            label="📥 Download Request JSON",
                            data=request_json,
                            file_name=f"request_{correlation_id}.json",
                            mime="application/json",
                            key=f"download_request_{correlation_id}"
                        )
                
                # Show response details
                if "response" in debug_context:
                    with st.expander("📥 Response Details"):
                        resp = debug_context["response"]
                        st.markdown(f"**Schema:** {resp.get('schema_name')}")
                        st.markdown(f"**Decision Notes:** {resp.get('decision_notes')}")
                        st.markdown(f"**Timestamp:** {resp.get('timestamp')}")
                        
                        # Show metadata
                        if "response_metadata" in resp:
                            meta = resp["response_metadata"]
                            st.markdown("**Response Metadata:**")
                            st.json(meta)
                        
                        # Show usage
                        if "usage" in resp:
                            usage = resp["usage"]
                            st.markdown("**Token Usage:**")
                            st.json(usage)
                        
                        # Show refusal if present
                        if "refusal" in resp:
                            st.error(f"**Refusal:** {resp['refusal']}")
                        
                        # Show output
                        if "output" in resp:
                            output = resp["output"]
                            st.markdown("**Output:**")
                            st.markdown(f"- Parsed: {output.get('parsed_present')}")
                            st.markdown(f"- Type: {output.get('parsed_type')}")
                            if output.get("text"):
                                st.text_area(
                                    "Output Text (truncated)",
                                    output["text"],
                                    height=150,
                                    key=f"output_{correlation_id}"
                                )
                        
                        # Show error if present
                        if "error" in resp:
                            err = resp["error"]
                            st.error(f"**Error:** {err.get('type')} - {err.get('message')}")
                        
                        # Download response JSON
                        response_json = json.dumps(resp, indent=2)
                        st.download_button(
                            label="📥 Download Response JSON",
                            data=response_json,
                            file_name=f"response_{correlation_id}.json",
                            mime="application/json",
                            key=f"download_response_{correlation_id}"
                        )
                
                # Show notes
                if "notes" in debug_context:
                    with st.expander("📝 Decision Notes"):
                        notes = debug_context["notes"]
                        st.json(notes)
            
            else:
                st.info("No debug files found. Set `CQC_OPENAI_DEBUG_SAVE_DIR` environment variable to save debug files.")
        
        # Add instructions
        st.markdown("---")
        st.markdown("""
        **Debug Mode Configuration:**
        - `CQC_OPENAI_DEBUG=1` - Enable debug mode
        - `CQC_OPENAI_DEBUG_REDACT=1` - Redact sensitive data (default: enabled)
        - `CQC_OPENAI_DEBUG_SAVE_DIR=/path/to/dir` - Save debug files to directory
        """)
