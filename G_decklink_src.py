#  Decklink source processor..... Powered by Gstreamer
#  
#  Managed by sendust
#  Last Edit : 2022/1/6
#
#  How to dynamically add remove filesink
#  https://forums.developer.nvidia.com/t/how-to-dynamically-add-remove-filesink/108821
#
#  You can try to drop the buffers using a buffer probe just before the filesink or encoder when no objects are present in the frame. Install a pad probe on the sink pad of the encoder and check if there are objects in the metadata and return GST_PAD_PROBE_OK or GST_PAD_PROBE_DROP
#
#
#
#
#
#    - mov (h264 video , pcm audio pipeline)
#  gst-launch-1.0  decklinkvideosrc mode=1080i5994 num-buffers=800 buffer-size=150 ! queue max-size-buffers=2000 max-size-bytes=8000000 max-size-time=5000000000 ! videoconvert n-threads=32 ! video/x-raw,format=I422_10LE ! x264enc interlaced=True key-int-max=1 bitrate=50000 pass="cbr" cabac=false trellis=false  ! qtmux name=mux ! queue ! filesink location=x264_nocabac2.mov decklinkaudiosrc num-buffers=800 buffer-size=150 ! queue max-size-buffers=2000 max-size-bytes=20000000 max-size-time=5000000000 ! audioconvert ! audio/x-raw,rate=48000,format=S24LE ! queue ! mux.
#
#    2022/1/13  test with x264enc, mov muxer (video and audio)
#    2022/1/27  add file logging
#               Large Queue size for performance
#    2022/4/19  Fix test signal mode (is-live True)
#               add osc udp send interval option
#               add GUI Start up X, Y position option
#    2022/4/27  Add rtmp preset
#               put pid as log filename
#
#




import gi, threading, time, socket, argparse, ctypes
import os, urllib.parse, sys, textwrap, datetime
from psutil import process_iter
from signal import SIGTERM # or SIGKILL


gi.require_version('Gst', '1.0')
gi.require_version('Gtk', '3.0')
gi.require_version("GstAudio", "1.0")

from gi.repository import Gst, Gtk, GLib, Gdk, GstAudio, GObject



TARGET_TYPE_URI_LIST = 80


# Script located folder must have 'graph' sub folder
os.environ["GST_DEBUG_DUMP_DOT_DIR"] = os.getcwd() + "\\graph\\"
os.putenv('GST_DEBUG_DUMP_DIR_DIR', os.getcwd() + "\\graph\\")

Gst.init(None)

      
        
        
class demuxer_pad():
    stream = []
    video = []                  # store video src pad list
    audio = []                  # store audio src pad list
    audio_mono = []             # store mono stream audio src pad list
    audio_stereo = []           # store stereo stream audio src pad list
    audio_multi = []
    audio_many_channel = 0      # store number of audio channel (single stream, multi channel)

  # Default queue size : 200, 10MB, 1Sec
def setup_queue_size(queue, size_buffer = 2000, size_mbyte = 100, size_time = 5):
    queue.set_property("max-size-buffers", size_buffer)
    queue.set_property("max-size-bytes", size_mbyte * 1000000)
    queue.set_property("max-size-time", size_time * Gst.SECOND)


def updatelog(*args, end="\r\n"):    # Added 2022/1/25
    global pid
    log_prefix = "G_Decklink_src_log_[" + str(pid) + "]_"
    if not os.path.exists(os.getcwd() + "\\log"):
        print("path not exist.. make one")
        os.mkdir(os.getcwd() + "\\log")
    filename_log = os.getcwd() + "\\log\\" + log_prefix + datetime.datetime.now().strftime("%Y-%m-%d") + ".log"
    #print("log file name is " + filename_log)
    result = ""
    # date_time = datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S ")
    for value in args:
        result += str(value)
    
    print(result, end=end)
    with open(filename_log, "a") as the_file:
        the_file.write(str(datetime.datetime.now()) + "  "  + result + "\n")   
           
            

def g_value_matrix(array_python = [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]):
    array_temp = []
    ch_in = len(array_python[0])
    ch_out = 0
    for i in array_python:
        updatelog("output ch {0} <--- input assign {1}".format(ch_out, i))
        array_float = [float(j) for j in i]
        if len(array_float) != ch_in:
            updatelog("Matrix array dimension error!! Abort transform")
            ch_in = 0
            return []
        array_float_gvalue = Gst.ValueArray(array_float)
        array_temp.append(array_float_gvalue)
        ch_out += 1
    if ch_in:   
        array_gvalue = Gst.ValueArray(array_temp)         # complex matrix, G_ValueArray expression...
    else:
        updatelog("Error matrix conversion")
        return []
        
    updatelog("transformed array is " , array_gvalue)
    updatelog("Matrix has {0} input channel, {1} output channel".format(ch_in, ch_out))
    return array_gvalue, ch_in, ch_out

def audio_mix_matrix(array_python = [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]):
    array_gvalue, ch_in, ch_out = g_value_matrix(array_python)
    amatrix = Gst.ElementFactory.make("audiomixmatrix")
    amatrix.set_property("in-channels", ch_in)
    amatrix.set_property("out-channels", ch_out)
    amatrix.set_property("matrix", array_gvalue)
    return  amatrix   


 
def queue_audio_converter_bin(name_bin, string_caps):
    updatelog(">> Create bin with name [{0}] and caps [{1}]".format(name_bin, string_caps))
    bin = Gst.Bin.new(name_bin)
    queue =  Gst.ElementFactory.make("queue")
    converter = Gst.ElementFactory.make("audioconvert")
    caps_filter =  Gst.ElementFactory.make("capsfilter")
    caps_filter.set_property("caps", Gst.Caps.from_string(string_caps))
    bin.add(queue)
    bin.add(converter)
    bin.add(caps_filter)
    queue.link(converter)
    converter.link(caps_filter)
    bin.add_pad(Gst.GhostPad("sink", queue.get_static_pad("sink")))
    bin.add_pad(Gst.GhostPad("src", caps_filter.get_static_pad("src")))
    return bin

def queue_audio_resample_converter_bin(name_bin, string_caps):
    updatelog(">> Create bin with name [{0}] and caps [{1}]".format(name_bin, string_caps))
    bin = Gst.Bin.new(name_bin)
    queue =  Gst.ElementFactory.make("queue")
    converter = Gst.ElementFactory.make("audioconvert")
    resample = Gst.ElementFactory.make("audioresample")
    caps_filter =  Gst.ElementFactory.make("capsfilter")
    caps_filter.set_property("caps", Gst.Caps.from_string(string_caps))
    
    bin.add(queue)
    bin.add(converter)
    bin.add(resample)
    bin.add(caps_filter)
    queue.link(resample)
    resample.link(converter)
    converter.link(caps_filter)
    
    bin.add_pad(Gst.GhostPad("sink", queue.get_static_pad("sink")))
    bin.add_pad(Gst.GhostPad("src", caps_filter.get_static_pad("src")))
    return bin
    
def queue_video_converter_bin(name_bin, string_caps):
    updatelog(">> Create bin with name [{0}] and caps [{1}]".format(name_bin, string_caps))
    cpu_count = os.cpu_count()
    bin = Gst.Bin.new(name_bin)
    queue =  Gst.ElementFactory.make("queue")
    #setup_queue_size(queue, 2000, 2000, 5)
    converter = Gst.ElementFactory.make("videoconvert")
    converter.set_property("n-threads", cpu_count)
    resize = Gst.ElementFactory.make("videoscale")
    resize.set_property("n-threads", cpu_count)
    rate = Gst.ElementFactory.make("videorate")
    caps_filter =  Gst.ElementFactory.make("capsfilter")
    caps_filter.set_property("caps", Gst.Caps.from_string(string_caps))
    
    bin.add(queue)
    bin.add(converter)
    bin.add(resize)
    bin.add(rate)
    bin.add(caps_filter)
    
    queue.link(resize)
    resize.link(converter)          # resize -> color space conversion  (for mininum cpu usage)
    converter.link(rate)
    rate.link(caps_filter)
    bin.add_pad(Gst.GhostPad("sink", queue.get_static_pad("sink")))
    bin.add_pad(Gst.GhostPad("src", caps_filter.get_static_pad("src")))
    return bin
    



def caps_filter(name, string_caps):
    updatelog(">> Create caps filter bin with name [{0}] and caps [{1}]".format(name, string_caps))
    caps =  Gst.ElementFactory.make("capsfilter", name)
    caps.set_property("caps", Gst.caps_from_string(string_caps))
    return caps



def print_field(field, value, pfx):
    str = Gst.value_serialize(value)
    updatelog("{0:s}  {1:15s}: {2:s}".format(
        pfx, GLib.quark_to_string(field), str))
    return True


def print_caps(caps, pfx):
    if not caps:
        return

    if caps.is_any():
        updatelog("{0:s}ANY".format(pfx))
        return

    if caps.is_empty():
        updatelog("{0:s}EMPTY".format(pfx))
        return

    for i in range(caps.get_size()):
        structure = caps.get_structure(i)
        updatelog("{0:s}{1:s}".format(pfx, structure.get_name()))
        structure.foreach(print_field, pfx)

# prints information about a pad template (including its capabilities)


def print_pad_templates_information(factory):
    updatelog("Pad templates for {0:s}".format(factory.get_name()))
    if factory.get_num_pad_templates() == 0:
        updatelog("  none")
        return

    pads = factory.get_static_pad_templates()
    for pad in pads:
        padtemplate = pad.get()

        if pad.direction == Gst.PadDirection.SRC:
            updatelog("  SRC template:", padtemplate.name_template)
        elif pad.direction == Gst.PadDirection.SINK:
            updatelog("  SINK template:", padtemplate.name_template)
        else:
            updatelog("  UNKNOWN template:", padtemplate.name_template)

        if padtemplate.presence == Gst.PadPresence.ALWAYS:
            updatelog("    Availability: Always")
        elif padtemplate.presence == Gst.PadPresence.SOMETIMES:
            updatelog("    Availability: Sometimes")
        elif padtemplate.presence == Gst.PadPresence.REQUEST:
            updatelog("    Availability: On request")
        else:
            updatelog("    Availability: UNKNOWN")

        if padtemplate.get_caps():
            updatelog("    Capabilities:")
            print_caps(padtemplate.get_caps(), "      ")

        updatelog("")

# shows the current capabilities of the requested pad in the given element


def print_pad_capabilities(element, pad_name):
    # retrieve pad
    pad = element.get_static_pad(pad_name)
    if not pad:
        updatelog("ERROR: Could not retrieve pad '{0:s}'".format(pad_name))
        return

    # retrieve negotiated caps (or acceptable caps if negotiation is not
    # yet finished)
    caps = pad.get_current_caps()
    if not caps:
        caps = pad.get_allowed_caps()

    # print
    updatelog("Caps for the {0:s} pad:".format(pad_name))
    print_caps(caps, "      ")



def do_audio_level_message_handler(message):
    structure_message = message.get_structure()
    #updatelog(structure_message)      # Display full message structure
    #updatelog(structure_message.get_name())
    try:
        rms_value = structure_message.get_value("rms")
    except:
        rms_value = [-700.0, -700.0]          # no audio stream
    i = 0
    for value in rms_value:  
        osc.volume[i] = value  # Setup osc volume data from level message
        i = i + 1
        
        
    end_time =  structure_message.get_value("endtime")
    timestamp =  structure_message.get_value("timestamp")
    stream_time =  structure_message.get_value("stream-time")
    running_time =  structure_message.get_value("running-time")
    duration_time =  structure_message.get_value("duration")


class myplayer():
    
    def __init__(self):
        updatelog("Create player class")
        
        self.pipeline = Gst.Pipeline.new("player-pipeline")
        #self.fakesink = Gst.ElementFactory.make("fakesink")
        #self.pipeline.add(self.fakesink)
        self.bus = self.pipeline.get_bus()
        self.element = {}
        
        self.demuxer_pads = demuxer_pad()
        
        self.is_playing = False
        self.clip = ""
        self.mark_in = 0
        self.length = 0
        self.preset = ""
        self.auto_start = 0
        
        self.pipeline_state = Gst.Element.state_get_name(Gst.State.PAUSED)      # Update from message
        self.state1 = "NONE"                                                    # Update from query
        self.state2 = "NONE"
        self.flag_seek = False
        self.count = 0
        self.decklink = 0               # Decklink device number (start from 0)
    
    def queue_event(self, queue):       # Print and monitoring queue status
        updatelog("There is queue event +++++++++++++++++++++++++++  " + queue.name)
        updatelog("current-level-buffers = " + str(queue.get_property("current-level-buffers")))
        updatelog("current-level-bytes   = " + str(queue.get_property("current-level-bytes")))
        updatelog("current-level-time    = " + str(queue.get_property("current-level-time")))


    def on_message(self, bus, message):
        #updatelog("Message received -> {}".format(message))
        mtype = message.type
        #updatelog("Message structure is {0}".format(message.get_structure()))
        
        if mtype == Gst.MessageType.EOS:
            # Handle End of Stream
            updatelog("End of stream")
            self.is_playing = False
            self.pipeline.set_state(Gst.State.PAUSED)
            
        elif mtype == Gst.MessageType.ERROR:
            # Handle Errors
            err, debug = message.parse_error()
            updatelog("ERROR:", message.src.get_name(), ":", err.message)
            updatelog(err, debug)
            
        elif mtype == Gst.MessageType.WARNING:
            # Handle warnings
            err, debug = message.parse_warning()
            updatelog(err, debug)
            
        elif mtype == Gst.MessageType.ELEMENT:
            # Handle element message
            element_source = message.src.get_name()
            #updatelog("Element message received, source is ---->  " + element_source)
            if element_source == "level0":  # level element name in pipeline
                do_audio_level_message_handler(message)
                
        elif mtype == Gst.MessageType.STATE_CHANGED:    
            old_state, new_state, pending_state = message.parse_state_changed()
            updatelog("Pipeline state changed from '{0:s}' to '{1:s}' / from src '{2}'".format(
                Gst.Element.state_get_name(old_state),
                Gst.Element.state_get_name(new_state), message.src.get_name()))
            if message.src.get_name() == "player-pipeline":         # update pipeline state
                self.pipeline_state = Gst.Element.state_get_name(new_state)
                updatelog("Update pipeline state <<------------------------------------->> [%s] " % self.pipeline_state)
                if (self.pipeline_state == "PAUSED"):
                    do_level_init()             # audio level to zero
                    if self.flag_seek:
                        self.flag_seek = False
                        do_seek_load()          # perform Accurate seek while load clip

        elif mtype == Gst.MessageType.TAG:      # Show tag message
            text = message.parse_tag().to_string()
            header = text[:120]   # Print header message only
            updatelog("Tag message received {0}".format(header))
            
        elif mtype == Gst.MessageType.QOS:      # Show tag message
            format, processed, dropped = message.parse_qos_stats()
            updatelog("Qos message received- {0} proc- {1} drop- {2}".format(format, processed, dropped))
        
        elif mtype == Gst.MessageType.NEW_CLOCK:   
            new_clock = message.parse_new_clock()
            updatelog("  +++ Clock source changed .. " , new_clock)
        
        else:
          updatelog("Bus Message RECEIVED {0}  ".format(mtype))

        return True
    

    def on_sync_message(self, bus, message):   
        self.count += 1
        updatelog("sync message received " + str(self.count), end="\r")
        pass        



    def load(self, file_source):
        updatelog("Load with file %s" % file_source)
        updatelog("Start osc timer")
        GLib.timeout_add(50, do_osc)    # the time between calls to the function, in milliseconds (1/1000ths of a second)


    def pad_added_handler(self, src, new_pad):     # mxf demuxer found new pad...........

        name_new_pad = new_pad.get_name()
        name_element = src.get_name()
        updatelog("Received new pad '%s' from '%s':" % (name_new_pad, name_element))
        #updatelog("Fake sink conn result is ", self.element[name_element].link_pads(name_new_pad, self.fakesink, "sink"))
        new_pad_type = new_pad.query_caps(None).to_string() # Get caps (All)
        current_cap = new_pad.get_current_caps()        # Get negotiated caps
            
        for i in range(current_cap.get_size()):
            structure = current_cap.get_structure(i)
            updatelog(structure)
        
        self.demuxer_pads.stream.append(name_new_pad)   # Store every pad name to stream array
        
        updatelog("current caps of pad is [", current_cap, "]")
        updatelog("   >> Demuxer src added with type ... {0}  \r\n".format(new_pad_type))
        if new_pad_type.startswith("audio/"):  # collect audio stream from demuxer
            self.demuxer_pads.audio.append(name_new_pad)
            number_channel = structure.get_value("channels")
            updatelog("  ***** Found {0} channel audio stream".format(number_channel))
            if number_channel == 1:
                self.demuxer_pads.audio_mono.append(name_new_pad)           # stream has mono channel audio
            if number_channel == 2:    
                self.demuxer_pads.audio_stereo.append(name_new_pad)         # stream has stereo channel audio
            if number_channel > 3:    
                self.demuxer_pads.audio_multi.append(name_new_pad)
                self.demuxer_pads.audio_many_channel = number_channel       # stream has multi channel audio
                
        if new_pad_type.startswith("video/"):  # collect video stream from demuxer
            self.demuxer_pads.video.append(name_new_pad)
        return


    def start_bus_message(self):
        self.bus.add_signal_watch()
        self.bus.connect("message", self.on_message)
        

    def play(self):
        self.is_playing = True
        updatelog("Set pipeline to PLAYING")
        self.pipeline.set_state(Gst.State.PLAYING)
  
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS, "python_dot_PLAY_debug")
    
    def pause(self): 
        self.is_playing = False
        self.pipeline.set_state(Gst.State.PAUSED)
        
        
    def update_position_old(self):
        if not self.is_playing:
            return False # cancel timeout
        else:
            set_scale()
        return True # continue calling every x milliseconds
        
    def update_position(self):
        set_scale()
        return True # continue calling every x milliseconds

    
class pipeline_test_source(myplayer):           # test signal source (for developing)

    def no_more_pad_handler(self, src):  
        pass


    def build_pipe(self):
    
        updatelog("<<< build pipeline for decklink source >>>")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
        
        self.element["source_v"] = Gst.ElementFactory.make("videotestsrc")
        self.element["source_v"].set_property("pattern", "smpte")
        self.element["source_v"].set_property("is-live", True)
        
        self.element["source_a"] = Gst.ElementFactory.make("audiotestsrc")
        self.element["source_a"].set_property("wave", "ticks")
        self.element["source_a"].set_property("is-live", True)
                
                
        self.element["caps_v"] = caps_filter("caps_test_v", "video/x-raw, width=1920, height=1080, framerate=30000/1001, format=UYVY")
        self.element["caps_a"] = caps_filter("caps_test_a", "audio/x-raw, channels=8, rate=48000, layout=interleaved")
        
        
        
        self.element["queue_v"] = Gst.ElementFactory.make("queue", "queue_v")
        self.element["queue_a"] = Gst.ElementFactory.make("queue", "queue_a")
        self.element["queue_gtk"] = Gst.ElementFactory.make("queue", "queue_gtk")
        
        setup_queue_size(self.element["queue_v"], 2000, 2000, 5)
        setup_queue_size(self.element["queue_gtk"], 2000, 2000, 5)
        
        
        self.element["tee_v"] = Gst.ElementFactory.make("tee", "tee_v")

        updatelog("\r\nrequest two pad from tee_v ---------------------------------")
        updatelog(self.element["tee_v"].request_pad(self.element["tee_v"].get_pad_template("src_%u")))  # pad name should be src_0
        #updatelog(self.element["tee_v"].request_pad(self.element["tee_v"].get_pad_template("src_%u")))
        
        self.tee_v_src_pad = self.element["tee_v"].get_request_pad("src_1")
        updatelog(self.tee_v_src_pad)
        
        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", True)               # async false introduce lots of qos message (drop frame).. changed 2022/4/20
        self.element["previewbin"] = queue_video_converter_bin("previewbin", "video/x-raw, width=400, height=224, format=BGRx")
        # self.element["previewbin"].sync_state_with_parent()

        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        self.element["sink_a"] =  Gst.ElementFactory.make("fakesink", "sink_fake")

        self.element["level"].set_property("interval", 0.1 * Gst.SECOND)
        
        for el in self.element:
            updatelog("Add element to pipeline " , el , self.pipeline.add(self.element[el]))

        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source_v"], self.element["caps_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["caps_v"], self.element["queue_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["source_a"], self.element["queue_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v"], self.element["tee_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee_v"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["queue_gtk"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_gtk"], self.element["sink_gtk"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["queue_a"], self.element["caps_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["caps_a"], self.element["level"]), end="  ")
        updatelog(Gst.Element.link(self.element["level"], self.element["sink_a"]))
             
        updatelog("Finish manual element link --------------------------------------")

         
        self.start_bus_message()
        #do_preview()       
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS, "test_source")
        


#   ========== RTMP Streaming ================
#gst-launch-1.0 decklinkvideosrc ! queue max_size_buffers=20000 max_size_time=10000000000 max_size_bytes=2500000000 ! videoconvert ! avdeinterlace ! videorate ! tee name=t t. ! queue max_size_buffers=20000 max_size_time=10000000000 max_size_bytes=500000000 ! fpsdisplaysink t. ! queue max_size_buffers=20000 max_size_time=10000000000 max_size_bytes=2500000000 ! x264enc bitrate=4000 tune=0x00000004 threads=16  ! mux. decklinkaudiosrc ! queue max_size_buffers=20000 max_size_time=10000000000 max_size_bytes=50000000 ! audioconvert ! audioresample ! avenc_aac !  flvmux name=mux skip-backwards-streams=true ! rtmpsink location="rtmp://210.216.76.120/live/gstreamer"
        
        
class pipeline_rtmp(myplayer):

    def no_more_pad_handler(self, src):  
        pass

    def build_pipe(self):
    
        updatelog("<<< build pipeline for decklink source >>>")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
        
        self.element["source_v"] = Gst.ElementFactory.make("decklinkvideosrc")
        self.element["source_a"] = Gst.ElementFactory.make("decklinkaudiosrc")
        
        self.element["queue_v_src"] = Gst.ElementFactory.make("queue", "queue_v_src00")
        self.element["queue_v_enc"] = Gst.ElementFactory.make("queue", "queue_v_enc00")
        self.element["queue_a_src"] = Gst.ElementFactory.make("queue", "queue_a_src00")
        self.element["queue_a_level"] = Gst.ElementFactory.make("queue", "queue_a_level00")
        self.element["queue_a_enc"] = Gst.ElementFactory.make("queue", "queue_a_enc00")
        
        list_queue = ["queue_v_src", "queue_v_enc", "queue_a_src", "queue_a_level", "queue_a_enc"]
        
        for el in list_queue:
            updatelog("Add queue watch event ... " + el)
            self.element[el].connect("overrun", self.queue_event)
            self.element[el].set_property("leaky", "downstream")
        
        self.element["video_convert"] = Gst.ElementFactory.make("videoconvert", "videoconvert00")
        self.element["deinterlace"] = Gst.ElementFactory.make("avdeinterlace", "avdeinterlace00")
        self.element["video_rate"] = Gst.ElementFactory.make("videorate", "video_rate00")
        self.element["audio_convert"] = Gst.ElementFactory.make("audioconvert", "audio_convert00")
        self.element["audio_resample"] = Gst.ElementFactory.make("audioresample", "audio_resample00")
        self.element["enc_v"] = Gst.ElementFactory.make("x264enc", "enc_v00")
        self.element["enc_a"] = Gst.ElementFactory.make("avenc_aac", "enc_a00")
        self.element["mux"] = Gst.ElementFactory.make("flvmux", "mux00")
        self.element["rtmpsink"] = Gst.ElementFactory.make("rtmpsink", "rtmpsink00")

        self.element["tee_v"] = Gst.ElementFactory.make("tee", "tee_v")
        self.element["tee_a"] = Gst.ElementFactory.make("tee", "tee_a")        
        
        #setup_queue_size(self.element["queue_v_src"], 2000, 2000, 5)
        setup_queue_size(self.element["queue_v_enc"], 2000, 2000, 10)
        setup_queue_size(self.element["queue_a_enc"], 200, 20, 10)
        
        updatelog("\r\nrequest two pad from tee  ---------------------------------")
        
        pad_template_tee = self.element["tee_v"].get_pad_template("src_%u")
        self.element["tee_v"].request_pad(pad_template_tee, "src_0")        # src pad for preview (video)
        self.element["tee_v"].request_pad(pad_template_tee, "src_1")   # src pad for recording
 

        pad_template_tee = self.element["tee_a"].get_pad_template("src_%u")
        self.element["tee_a"].request_pad(pad_template_tee, "src_0")         # src pad for preview (audio)
        self.element["tee_a"].request_pad(pad_template_tee, "src_1")   # src pad for recording
 

        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", False)
        self.element["previewbin"] = queue_video_converter_bin("previewbin", "video/x-raw, width=400, height=224")

        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        self.element["sink_a"] =  Gst.ElementFactory.make("fakesink", "sink_fake")

        updatelog("Setup video encoder  & important element's parameter --")
        #self.element["enc_v"].set_property("bitrate", 4000)
        self.element["enc_v"].set_property("speed-preset", "fast")
        self.element["enc_v"].set_property("tune", 0x00000004)      # zero latency
        self.element["enc_v"].set_property("key-int-max", 15)
        
        self.element["enc_v"].set_property("threads", cpu_count)
        self.element["video_convert"].set_property("n-threads", cpu_count)

        self.element["tee_v"].set_property("allow-not-linked", True)
        self.element["tee_a"].set_property("allow-not-linked", True)
        self.element["level"].set_property("interval", 0.05 * Gst.SECOND)
        self.element["source_a"].set_property("channels", 2)        # set audio channel number
        self.element["mux"].set_property("streamable", True)        # set flv mux property
        self.element["mux"].set_property("skip-backwards-streams", True)        # set flv mux property
    
        #self.element["rtmpsink"].set_property("location", "rtmp://210.216.76.120/live/gstreamer")

        
        for el in self.element:
            updatelog("Add element to pipeline " , el , " --> " , self.pipeline.add(self.element[el]))

        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source_v"], self.element["queue_v_src"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v_src"], self.element["video_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert"], self.element["deinterlace"]), end="  ")
        updatelog(Gst.Element.link(self.element["deinterlace"], self.element["video_rate"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_rate"], self.element["tee_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee_v"], self.element["queue_v_enc"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v_enc"], self.element["enc_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["enc_v"], self.element["mux"]), end="  ")
        updatelog(Gst.Element.link(self.element["mux"], self.element["rtmpsink"]), end="  ")

        updatelog(Gst.Element.link(self.element["tee_v"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")

        updatelog(Gst.Element.link(self.element["source_a"], self.element["queue_a_src"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_a_src"], self.element["audio_resample"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_resample"], self.element["audio_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_convert"], self.element["tee_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee_a"], self.element["queue_a_level"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_a_level"], self.element["level"]), end="  ")
        updatelog(Gst.Element.link(self.element["level"], self.element["sink_a"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["tee_a"], self.element["queue_a_enc"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_a_enc"], self.element["enc_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["enc_a"], self.element["mux"]), end="  ")

             
        updatelog("Finish manual element link --------------------------------------")

        #do_preview()        
        self.start_bus_message()
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS, "decklink_source")
    
    def set_decklink_number(self, device_number):
        updatelog("Setup decklink device number " , device_number)
        self.element["source_v"].set_property("device-number", device_number)
        self.element["source_a"].set_property("device-number", device_number)
        #self.element["source_v"].set_property("duplex-mode", "half")
    
    def set_rtmp_url(self, url):
        updatelog("Setup rtmp url ... " + url)
        self.element["rtmpsink"].set_property("location", url)
    
    def set_rtmp_bitrate(self, bitrate):
        updatelog("Setup rtmp bitrate ... " + str(bitrate))
        self.element["enc_v"].set_property("bitrate", bitrate)


        
class pipeline_rtmp_dual(myplayer):

    def no_more_pad_handler(self, src):  
        pass

    def build_pipe(self):
    
        updatelog("<<< build pipeline for decklink source >>>")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
        
        self.element["source_v"] = Gst.ElementFactory.make("decklinkvideosrc")
        self.element["source_a"] = Gst.ElementFactory.make("decklinkaudiosrc")
        
        self.element["queue_v_src"] = Gst.ElementFactory.make("queue", "queue_v_src00")
        self.element["queue_v_enc"] = Gst.ElementFactory.make("queue", "queue_v_enc00")
        self.element["queue_a_src"] = Gst.ElementFactory.make("queue", "queue_a_src00")
        self.element["queue_a_level"] = Gst.ElementFactory.make("queue", "queue_a_level00")
        self.element["queue_a_enc"] = Gst.ElementFactory.make("queue", "queue_a_enc00")
        self.element["queue_rtmp1_v"] = Gst.ElementFactory.make("queue", "queue_rtmp1_v00")
        self.element["queue_rtmp1_a"] = Gst.ElementFactory.make("queue", "queue_rtmp1_a00")
        self.element["queue_rtmp2_v"] = Gst.ElementFactory.make("queue", "queue_rtmp2_v00")
        self.element["queue_rtmp2_a"] = Gst.ElementFactory.make("queue", "queue_rtmp2_a00")
        
        list_queue = ["queue_v_src", "queue_v_enc", "queue_a_src", "queue_a_level", "queue_a_enc", "queue_rtmp1_v", "queue_rtmp1_a", "queue_rtmp2_v",  "queue_rtmp2_a"]
        
        for el in list_queue:
            updatelog("Add queue watch event ... " + el)
            self.element[el].connect("overrun", self.queue_event)
            self.element[el].set_property("leaky", "downstream")
        
        self.element["video_convert"] = Gst.ElementFactory.make("videoconvert", "videoconvert00")
        self.element["deinterlace"] = Gst.ElementFactory.make("avdeinterlace", "avdeinterlace00")
        self.element["video_rate"] = Gst.ElementFactory.make("videorate", "video_rate00")
        self.element["audio_convert"] = Gst.ElementFactory.make("audioconvert", "audio_convert00")
        self.element["audio_resample"] = Gst.ElementFactory.make("audioresample", "audio_resample00")
        self.element["enc_v"] = Gst.ElementFactory.make("x264enc", "enc_v00")
        self.element["enc_a"] = Gst.ElementFactory.make("avenc_aac", "enc_a00")
        self.element["mux1"] = Gst.ElementFactory.make("flvmux", "mux00")
        self.element["mux2"] = Gst.ElementFactory.make("flvmux", "mux01")
        self.element["rtmpsink1"] = Gst.ElementFactory.make("rtmpsink", "rtmpsink00")
        self.element["rtmpsink2"] = Gst.ElementFactory.make("rtmpsink", "rtmpsink01")

        self.element["tee_v"] = Gst.ElementFactory.make("tee", "tee_v")
        self.element["tee_a"] = Gst.ElementFactory.make("tee", "tee_a")
        self.element["tee_v_mux"] = Gst.ElementFactory.make("tee", "tee_v_mux")
        self.element["tee_a_mux"] = Gst.ElementFactory.make("tee", "tee_a_mux")
  
        setup_queue_size(self.element["queue_v_enc"], 2000, 2000, 10)
        setup_queue_size(self.element["queue_a_enc"], 200, 20, 10)
        
        updatelog("\r\nrequest two pad from tee  ---------------------------------")
        
        pad_template_tee = self.element["tee_v"].get_pad_template("src_%u")
        self.element["tee_v"].request_pad(pad_template_tee, "src_0")        # src pad for preview (video)
        self.element["tee_v"].request_pad(pad_template_tee, "src_1")        # src pad for streaming
 

        pad_template_tee = self.element["tee_a"].get_pad_template("src_%u")
        self.element["tee_a"].request_pad(pad_template_tee, "src_0")        # src pad for preview (audio)
        self.element["tee_a"].request_pad(pad_template_tee, "src_1")        # src pad for streaming
 
        pad_template_tee = self.element["tee_v_mux"].get_pad_template("src_%u")
        self.element["tee_v_mux"].request_pad(pad_template_tee, "src_0")         # first target
        self.element["tee_v_mux"].request_pad(pad_template_tee, "src_1")         # second target
        
        pad_template_tee = self.element["tee_a_mux"].get_pad_template("src_%u")
        self.element["tee_a_mux"].request_pad(pad_template_tee, "src_0")         # first target
        self.element["tee_a_mux"].request_pad(pad_template_tee, "src_1")         # second target

        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", False)
        self.element["previewbin"] = queue_video_converter_bin("previewbin", "video/x-raw, width=400, height=224")

        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        self.element["sink_a"] =  Gst.ElementFactory.make("fakesink", "sink_fake")

        updatelog("Setup video encoder  & important element's parameter --")
        #self.element["enc_v"].set_property("bitrate", 4000)
        self.element["enc_v"].set_property("speed-preset", "fast")
        self.element["enc_v"].set_property("tune", 0x00000004)      # zero latency
        self.element["enc_v"].set_property("key-int-max", 15)
        
        self.element["enc_v"].set_property("threads", cpu_count)
        self.element["video_convert"].set_property("n-threads", cpu_count)

        self.element["tee_v"].set_property("allow-not-linked", True)
        self.element["tee_a"].set_property("allow-not-linked", True)
        self.element["level"].set_property("interval", 0.05 * Gst.SECOND)
        self.element["source_a"].set_property("channels", 2)        # set audio channel number
        self.element["mux1"].set_property("streamable", True)        # set flv mux property
        self.element["mux1"].set_property("skip-backwards-streams", True)        # set flv mux property
        self.element["mux2"].set_property("streamable", True)        # set flv mux property
        self.element["mux2"].set_property("skip-backwards-streams", True)        # set flv mux property
    
        #self.element["rtmpsink"].set_property("location", "rtmp://210.216.76.120/live/gstreamer")

        
        for el in self.element:
            updatelog("Add element to pipeline " , el , " --> " , self.pipeline.add(self.element[el]))

        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source_v"], self.element["queue_v_src"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v_src"], self.element["video_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert"], self.element["deinterlace"]), end="  ")
        updatelog(Gst.Element.link(self.element["deinterlace"], self.element["video_rate"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_rate"], self.element["tee_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee_v"], self.element["queue_v_enc"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v_enc"], self.element["enc_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["enc_v"], self.element["tee_v_mux"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee_v_mux"], self.element["queue_rtmp1_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee_v_mux"], self.element["queue_rtmp2_v"]), end="  ")
  

        updatelog(Gst.Element.link(self.element["tee_v"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")

        updatelog(Gst.Element.link(self.element["source_a"], self.element["queue_a_src"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_a_src"], self.element["audio_resample"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_resample"], self.element["audio_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_convert"], self.element["tee_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee_a"], self.element["queue_a_level"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_a_level"], self.element["level"]), end="  ")
        updatelog(Gst.Element.link(self.element["level"], self.element["sink_a"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["tee_a"], self.element["queue_a_enc"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_a_enc"], self.element["enc_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["enc_a"], self.element["tee_a_mux"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee_a_mux"], self.element["queue_rtmp1_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee_a_mux"], self.element["queue_rtmp2_a"]), end="  ")

        updatelog(Gst.Element.link(self.element["queue_rtmp1_v"], self.element["mux1"]), end="  ")        
        updatelog(Gst.Element.link(self.element["queue_rtmp1_a"], self.element["mux1"]), end="  ")        
        updatelog(Gst.Element.link(self.element["queue_rtmp2_v"], self.element["mux2"]), end="  ")        
        updatelog(Gst.Element.link(self.element["queue_rtmp2_a"], self.element["mux2"]), end="  ")        

        updatelog(Gst.Element.link(self.element["mux1"], self.element["rtmpsink1"]), end="  ")        
        updatelog(Gst.Element.link(self.element["mux2"], self.element["rtmpsink2"]), end="  ")        
        
        updatelog("Finish manual element link --------------------------------------")

        #do_preview()        
        self.start_bus_message()
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS, "decklink_source")
    
    def set_decklink_number(self, device_number):
        updatelog("Setup decklink device number " , device_number)
        self.element["source_v"].set_property("device-number", device_number)
        self.element["source_a"].set_property("device-number", device_number)
        #self.element["source_v"].set_property("duplex-mode", "half")
    
    def set_rtmp_url(self, url):
        updatelog("Setup rtmp url ... " + url)
        self.element["rtmpsink1"].set_property("location", url)
    
    def set_rtmp_url2(self, url):
        updatelog("Setup rtmp url ... " + url)
        self.element["rtmpsink2"].set_property("location",url)

    def set_rtmp_bitrate(self, bitrate):
        updatelog("Setup rtmp bitrate ... " + str(bitrate))
        self.element["enc_v"].set_property("bitrate", bitrate)


        
class pipeline_decklink_source(myplayer):

    def no_more_pad_handler(self, src):  
        pass


    def build_pipe(self):
    
        updatelog("<<< build pipeline for decklink source >>>")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
        
        self.element["source_v"] = Gst.ElementFactory.make("decklinkvideosrc")
        self.element["source_a"] = Gst.ElementFactory.make("decklinkaudiosrc")
        
        self.element["queue_v"] = Gst.ElementFactory.make("queue", "queue_v")
        self.element["queue_a"] = Gst.ElementFactory.make("queue", "queue_a")
        
        self.element["tee_v"] = Gst.ElementFactory.make("tee", "tee_v")
        self.element["tee_a"] = Gst.ElementFactory.make("tee", "tee_a")
        
        setup_queue_size(self.element["queue_v"], 2000, 2000, 5)
        setup_queue_size(self.element["queue_a"], 2000, 200, 5)
        
        updatelog("\r\nrequest two pad from tee  ---------------------------------")
        
        pad_template_tee = self.element["tee_v"].get_pad_template("src_%u")
        self.element["tee_v"].request_pad(pad_template_tee, "src_0")        # src pad for preview (video)
        self.tee_v_src_pad = self.element["tee_v"].request_pad(pad_template_tee, "src_1")   # src pad for recording
 

        pad_template_tee = self.element["tee_a"].get_pad_template("src_%u")
        self.element["tee_a"].request_pad(pad_template_tee, "src_0")         # src pad for preview (audio)
        self.tee_a_src_pad = self.element["tee_a"].request_pad(pad_template_tee, "src_1")   # src pad for recording
 

        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", False)
        self.element["previewbin"] = queue_video_converter_bin("previewbin", "video/x-raw, width=400, height=224")
        self.element["previewbin"].sync_state_with_parent()

        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        self.element["sink_a"] =  Gst.ElementFactory.make("fakesink", "sink_fake")



        self.element["source_v"].set_property("buffer-size", 300)   # buffer size in frame (default 5)
        self.element["source_a"].set_property("buffer-size", 300)   # buffer size in frame (default 5)
        #self.element["source_v"].set_property("num-buffers", 300)  # send EOS after xxx frame... (For development)
        #self.element["source_a"].set_property("num-buffers", 300)  
        self.element["tee_v"].set_property("allow-not-linked", True)
        self.element["tee_a"].set_property("allow-not-linked", True)
        self.element["level"].set_property("interval", 0.05 * Gst.SECOND)
        self.element["source_a"].set_property("channels", 16)        # set audio channel number
    



        
        for el in self.element:
            updatelog("Add element to pipeline " , el , self.pipeline.add(self.element[el]))

        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source_v"], self.element["queue_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["source_a"], self.element["queue_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v"], self.element["tee_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee_v"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["queue_a"], self.element["tee_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee_a"], self.element["level"]), end="  ")
        updatelog(Gst.Element.link(self.element["level"], self.element["sink_a"]))
             
        updatelog("Finish manual element link --------------------------------------")

        #do_preview()        
        self.start_bus_message()
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS, "decklink_source")
    
    def set_decklink_number(self, device_number):
        updatelog("Setup decklink device number " , device_number)
        self.element["source_v"].set_property("device-number", device_number)
        self.element["source_a"].set_property("device-number", device_number)
        #self.element["source_v"].set_property("duplex-mode", "half")
    

      
class bin_file_rec():

    def __init__(self):
        self.bin = Gst.Bin.new("recbin")
        self.element = {}
        self.is_recording = False
        
    def no_more_pad_handler(self, src):  
        pass

    def build_pipe(self):
    
        updatelog("<<< build pipeline for decklink recorder >>>")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
               
        self.element["queue_v"] = Gst.ElementFactory.make("queue", "queue_v")
        self.element["convert_v"] = Gst.ElementFactory.make("videoconvert", "convert_v")
        self.element["encoder_v"] = Gst.ElementFactory.make("x264enc")
        setup_queue_size(self.element["queue_v"], 2000, 2000, 5)
        
        self.element["queue_a"] = Gst.ElementFactory.make("queue", "queue_a")
        self.element["convert_a"] = Gst.ElementFactory.make("audioconvert", "convert_a")
        self.element["caps_a"] = caps_filter("caps_audio", "audio/x-raw,rate=48000,format=S24LE")
        setup_queue_size(self.element["queue_a"], 2000, 200, 5)

        
        self.element["mux"] = Gst.ElementFactory.make("qtmux")
        self.element["queue_f"] = Gst.ElementFactory.make("queue", "queue_f")
        setup_queue_size(self.element["queue_f"], 2000, 200, 5)
        self.element["filesink"] = Gst.ElementFactory.make("filesink")        
        
        
        self.element["queue_v"].set_property("flush-on-eos", True)   # For fast finializing
        self.element["queue_a"].set_property("flush-on-eos", True)   # For fast finializing
        self.element["filesink"].set_property("location", "test2.mov")
        
        
        # Setup Video Encoder parameter ---------------------
        self.element["encoder_v"].set_property("interlaced", True)
        self.element["encoder_v"].set_property("key-int-max", 1)
        self.element["encoder_v"].set_property("bitrate", 50000)
        self.element["encoder_v"].set_property("pass", "cbr")
        self.element["encoder_v"].set_property("cabac", False)
        self.element["encoder_v"].set_property("trellis", False)
        
        
        updatelog("add file sink buffer probe (for rec frame count calculation)")
        updatelog(self.element["convert_v"].get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER , cb_rec))

        
        for el in self.element:
            updatelog("Add element to pipeline " , el , self.bin.add(self.element[el]))

        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["queue_v"], self.element["convert_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["convert_v"], self.element["encoder_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["encoder_v"], self.element["mux"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["queue_a"], self.element["convert_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["convert_a"], self.element["caps_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["caps_a"], self.element["mux"]), end="  ")        
        
        updatelog(Gst.Element.link(self.element["mux"], self.element["queue_f"]))
        updatelog(Gst.Element.link(self.element["queue_f"], self.element["filesink"]))

        updatelog("Finish manual element link ------------probetype--------------------------")

        updatelog("Create sink ghost pad")
        updatelog(self.bin.add_pad(Gst.GhostPad("sink_v", self.element["queue_v"].get_static_pad("sink"))))
        updatelog(self.bin.add_pad(Gst.GhostPad("sink_a", self.element["queue_a"].get_static_pad("sink"))))
        
        
    def set_filesink(self, fullpath):
        self.element["filesink"].set_property("location", fullpath)

    def get_bin(self):
        return self.bin

                
        
def cb_rec(pad, probe_info):        # callback for frame count while recording
    global gui, recorder
    recorder.frame_count += 1
    gui.label1.set_text(str(recorder.frame_count))
    
    return Gst.PadProbeReturn.OK    
        
        
def do_button1():              # press button1
    global player
    Gst.debug_bin_to_dot_file(player.pipeline,  Gst.DebugGraphDetails.STATES | Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "Dump_play_" + time.strftime("%Y%m%d-%H%M%S"))
    
 
def do_load():
    global gui, player, osc
    player.load(gui.entry_get_text())
    updatelog("player pipeline state while load is {0}".format(player.pipeline.get_state(Gst.CLOCK_TIME_NONE)))
    gui.set_title("G Engine Decklink Source //  " + player.clip)
    gui.button2.set_label("Start REC")


def do_button2():          # press button2      start or stop recording
    global recorder
    if (recorder.is_recording):
        do_eos()
    else:
        do_rec()
    

def do_button3():
    global recorder
    recorder.get_bin().send_event(Gst.Event.new_eos())
    recorder.get_bin().set_state(Gst.State.NULL)


def do_rec():               # start recording
    updatelog("Execute start rec")
    global player, recorder, gui
    recorder.frame_count = 0
    recorder.set_filesink("test" + time.strftime("%Y%m%d-%H%M%S") + ".mov")
    player.pipeline.add(recorder.get_bin())
    updatelog("connect tee with recorder bin")
    player.tee_v_src_pad.link(recorder.get_bin().get_static_pad("sink_v"))
    player.tee_a_src_pad.link(recorder.get_bin().get_static_pad("sink_a"))

    result = recorder.get_bin().set_state(Gst.State.PLAYING)
    if (result != Gst.StateChangeReturn.FAILURE):
        recorder.is_recording = True
        gui.button2.set_label("Stop REC")
    else:
        recorder.is_recording = False
        try:
            recorder.get_bin().set_state(Gst.State.NULL)
            player.pipeline.remove(recorder.get_bin())
        except:
            pass
        updatelog("error start recording")
        gui.button2.set_label("Start REC")

    
def do_eos():                 # press button3     stop recording
    updatelog("Execute stop rec")
    
    global player, recorder
    #player.pipeline.set_state(Gst.State.PAUSED)
    player.tee_v_src_pad.add_probe(Gst.PadProbeType.IDLE, cb_stop_rec_v)
    player.tee_a_src_pad.add_probe(Gst.PadProbeType.IDLE, cb_stop_rec_a)

    threading.Timer(0.5, do_remove_pipe).start()  ## Restore minimized GTK window

def cb_stop_rec_v(pad, probe_info):
    global player, recorder, gui
    updatelog("  <<<<< stop rec callback(video) ...\r\n   <<<<< unlink tee and recorder bin")    
    recorder.get_bin().get_static_pad("sink_v").send_event(Gst.Event.new_eos())
    updatelog(player.tee_v_src_pad.unlink(recorder.get_bin().get_static_pad("sink_v")))
    #recorder.get_bin().send_event(Gst.Event.new_eos())
    return Gst.PadProbeReturn.REMOVE            # Remove this callback


def cb_stop_rec_a(pad, probe_info):
    global player, recorder, gui
    updatelog("  <<<<< stop rec callback(audio) ...\r\n   <<<<< unlink tee and recorder bin")    
    recorder.get_bin().get_static_pad("sink_a").send_event(Gst.Event.new_eos())
    updatelog(player.tee_a_src_pad.unlink(recorder.get_bin().get_static_pad("sink_a")))
    return Gst.PadProbeReturn.REMOVE            # Remove this callback


def do_remove_pipe():
    global recorder, player
    updatelog("\r\n  ---> Send EOS singal on recorder bin")
    time.sleep(0.01)
    recorder.get_bin().set_state(Gst.State.PAUSED)
    recorder.get_bin().set_state(Gst.State.READY)
    recorder.get_bin().set_state(Gst.State.NULL)
    player.pipeline.remove(recorder.get_bin())
    recorder.is_recording = False
    gui.button2.set_label("Start REC")    



def cb_stop_rec_a_backup(pad, probe_info):
    global player, recorder, gui
    updatelog("  <<<<< stop rec callback(audio) ...\r\n   <<<<< unlink tee and recorder bin")    
    player.tee_a_src_pad.unlink(recorder.get_bin().get_static_pad("sink_a"))    
    
    # check if video pad is unlinked    
    if not player.element["tee_v"].get_static_pad("src_1").is_linked():
        updatelog("\r\n <<< video pad already disconnected... send EOS signal....")
        recorder.get_bin().send_event(Gst.Event.new_eos())
        recorder.get_bin().set_state(Gst.State.NULL)
        player.pipeline.remove(recorder.get_bin())
        recorder.is_recording = False
        gui.button2.set_label("Start REC")  
    
    return Gst.PadProbeReturn.REMOVE            # Remove this callback


def do_level_init():
    osc.init_volume()
    

class debugwindow(Gtk.Window):
    def __init__(self):
        updatelog("Create debug window class  --------------- ")    
        super().__init__(title="Debug window")
        self.set_size_request(500, 400)  
        self.connect("destroy", self.winclose)
        box = Gtk.Box(spacing=6)
        box.set_size_request(900,400)
        self.add(box)
        self.label1 = Gtk.Label.new("Debug screen1")
        self.label1.set_size_request(450,400)
        self.label1.set_xalign(0.01)
        self.label1.set_yalign(0.01)
        self.label2 = Gtk.Label.new("Debug screen2")
        self.label2.set_size_request(450,400)
        self.label2.set_xalign(0.01)
        self.label2.set_yalign(0.01)
        self.label2.set_line_wrap(True)
        self.label2.set_line_wrap_mode(1)
        self.label2.set_max_width_chars(100)
        box.pack_start(self.label1, True, True, 0)
        box.pack_start(self.label2, True, True, 0)
        self.text_list = ["Information"]
        self.set_resizable(False)
        
        
    def show_gui(self):
        self.show_all()   
        
    def hide_gui(self):
        self.hide()   
    
    def append_text(self, text):
        self.text_list.append(str(time.perf_counter()) + " " + text)
        if (len(self.text_list) > 20):
            new_list = self.text_list[1:]        # Remove first line
            self.text_list = new_list
            
        str_show = ""
        for line in self.text_list:
            str_show += line + "\n"
            
        self.label2.set_text(str_show)
    
    def winclose(self):
        self.close()
        
        
    
class mywindow(Gtk.Window):
    def __init__(self):
        updatelog("Create GUI window class")
        super().__init__(title="G Engine Decklink Source")

        self.set_size_request(420, 420)  
        #self.set_border_width(10)
        self.connect("destroy", os_exit)
        #self.connect('realize', self.on_realize)
        self.layout = Gtk.Layout()
        self.layout.set_size(420, 420)
        
        self.button1 = Gtk.Button(label="1")
        self.button1.connect("clicked", self.on_button1)
        self.layout.put(self.button1, 10, 15)

        self.button2 = Gtk.Button(label="2")
        self.button2.connect("clicked", self.on_button2)
        self.layout.put(self.button2, 60, 15)

        self.button3 = Gtk.Button(label="3")
        self.button3.connect("clicked", self.on_button3)
        self.layout.put(self.button3, 150, 15)

        self.label1 = Gtk.Label.new("Debug info")
        self.layout.put(self.label1, 220, 15)
        
        self.label2 = Gtk.Label.new("Debug info2")
        self.layout.put(self.label2, 220, 35)
        
        self.entry = Gtk.Entry.new()
        self.entry.set_size_request(400, 10)
        self.entry.set_text("Drag drop Media file here")
        self.layout.put(self.entry, 10, 60)

        self.levelbar = []
        for i in range(16):
            self.levelbar.append(Gtk.ProgressBar.new())
            self.levelbar[i].set_orientation(Gtk.Orientation.VERTICAL)
            self.levelbar[i].set_inverted(True)
            self.levelbar[i].set_size_request(10,80)
            self.layout.put(self.levelbar[i], 16 + i * 25, 330)
            self.levelbar[i].set_fraction(0.1)
        
        
        self.connect('drag_data_received', on_drag_data_received)
        self.drag_dest_set( Gtk.DestDefaults.MOTION|
                  Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
                  [Gtk.TargetEntry.new("text/uri-list", 0, 80)], Gdk.DragAction.COPY)


        self.add(self.layout)
        
        self.set_resizable(False)       # disable window resize

        
        
    def on_realize(self):
        do_preview()            # show gtksink preview window

    def on_button1(self, widget):       # Goto Top button
        updatelog("button1 Pressed")
        self.entry_get_text()
        do_button1()
            
    def on_button2(self, widget):   
        updatelog("button2 Pressed")
        do_button2()
        
    def on_button3(self, widget):  
        updatelog("button3 Pressed")
        do_button2()

    def on_slider_seek(self, widget):
        updatelog("slider move  " + str(self.slider.get_value()))
        do_slider_seek()
        
    def run(self):
        self.show_all()
        #self.move(10,10)
        Gtk.main()

    def entry_get_text(self):
        text = self.entry.get_text()
        updatelog("Entry text is " + text)
        return text
        
    def entry_set_text(self, text = "test text"):
        text = self.entry.set_text(text)
        updatelog("New text is " + str(text))

    def move_primary(self, x, y):
        rect2 = self.get_screen().get_display().get_monitor(0).get_geometry()   # Get primary monitor geometry (x,y,width,height)
        self.move(rect2.x + x, rect2.y + y)
        
        
        
        
class GenericException(Exception):
    pass


class osc_like_udp_info():
    def __init__(self, host="127.0.0.1", port=5253):
        updatelog("Create osc class with port " , port)
        self.host = host
        self.port = port
        
        self.volume = [-700,-700,-700,-700,-700,-700,-700,-700,-700,-700,-700,-700,-700,-700,-700,-700] # 8 channel audio level meter array
        self.mark_in = 0
        self.length = 0
        self.name_foreground = ""
        self.name_background = ""
        self.position_run = ""
        self.smpte_dur = ""
        self.smpte_rem = ""
        self.smpte_run = ""
        self.time_rem = ""
        self.time_run = ""
        self.time_dur = ""        
        self.time_tick = ""
        self.loop = ""
        self.state1 = ""
        self.state2 = ""
        self.count_tick = 0         # Variable for adjust UDP send interval (fast <----> slow)
        self.modulo = 1
    
    def set_udp_interval(self, value):
        self.modulo = value

    def init_volume(self):
        self.volume = [-700,-700,-700,-700,-700,-700,-700,-700,-700,-700,-700,-700,-700,-700,-700,-700] # 8 channel audio level meter array
    
    def send_udp(self):
        self.text_send = ""
        try:
            self.text_send = ("mark_in**" + str(self.mark_in)
                        + "\nlength**" + str(self.length)
                        + "\nname_foreground**" + str(self.name_foreground)
                        + "\nname_background**" + str(self.name_background)
                        + "\nposition_run**" + str(self.position_run)
                        + "\nsmpte_dur**" + str(self.smpte_dur)
                        + "\nsmpte_rem**" + str(self.smpte_rem)
                        + "\nsmpte_run**" + str(self.smpte_run)
                        + "\ntime_rem**" + str(self.time_rem)
                        + "\ntime_run**" + str(self.time_run)
                        + "\ntime_dur**" + str(self.time_dur)
                        + "\ntime_tick_engine**" + str(time.perf_counter())
                        + "\nloop**" + str(self.loop)
                        + "\nstate1**" + str(self.state1)
                        + "\nstate2**" + str(self.state2)
                        + "\nout_time_ms**" + str(int(self.time_run * 1000000))      # New  2022/4/20, ffmpeg compatible                    
                        + "\nvolume1**" + str(self.volume[0])
                        + "\nvolume2**" + str(self.volume[1])
                        + "\nvolume3**" + str(self.volume[2])
                        + "\nvolume4**" + str(self.volume[3])
                        + "\nvolume5**" + str(self.volume[4])
                        + "\nvolume6**" + str(self.volume[5])
                        + "\nvolume7**" + str(self.volume[6])
                        + "\nvolume8**" + str(self.volume[7]))
        
        except Exception as e:
            updatelog("Raise exception while creating udp text" + str(e))
        
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(bytes(self.text_send, "utf-8"), (self.host, self.port))
                       
def do_osc():
    global player, osc, gui, recorder, debug_gui
    
    osc.name_foreground = player.clip
    #result, state1, state2 = player.pipeline.get_state(0.002 * Gst.SECOND)
    
    #updatelog("pipeline is {0} <--> {1} <--> {2}".format(result.value_name, state1.value_name, state2.value_name))
    
    #if result.value_name == "GST_STATE_CHANGE_SUCCESS":
    #    player.state1 = state1.value_name
    #    player.state2 = state2.value_name
    #    osc.state1 = player.state1
    #    osc.state2 = player.state2
    #else:
    #    updatelog("Fail to get pipeline state  /////////////////")
        
    success, duration = player.pipeline.query_duration(Gst.Format.TIME)         # duration ; real duration of clip
    if not success:                                                            # length ; playback length limited by argument
        print("do_osc>> Couldn't fetch song duration   -- tick.." + str(time.perf_counter()), end="\r")
        return True
    else:
        osc.time_dur = duration / Gst.SECOND
        #if (player.length > osc.time_dur) | (int(player.length) == 0):  # acquire new length from pipeline query
            
    success, position = player.pipeline.query_position(Gst.Format.TIME)
    
    if success:
        osc.time_run = position / Gst.SECOND

    i = 0
    for value in osc.volume:
        gui.levelbar[i].set_fraction(1.08 ** value * 3)
        i = i + 1
    
    volume_int = [ int(x) for x in osc.volume]    # convert short number to print console
    print("{3} / is_recording {0} time_run {1} audio level {2}     ".format(recorder.is_recording, osc.time_run, volume_int, player.pipeline_state), end="\r")
    
    osc.mark_in = player.mark_in
    osc.length = player.length
    
    try:
        osc.time_rem = player.length - (position / Gst.SECOND - player.mark_in)
    except:
        updatelog("Fail to get [time_rem]")
    osc.state1 = player.pipeline_state
    osc.count_tick += 1
    if not (osc.count_tick % osc.modulo):        # Adjust udp send interval
        osc.send_udp()
        debug_gui.label1.set_text(osc.text_send)
    
    return True         # return true for periodic timer
    
    
   
def on_drag_data_received(widget, context, x, y, selection, target_type, timestamp):
    global gui
    updatelog("Drag drop detected")
    updatelog("Target type is " + str(target_type))
    if target_type == TARGET_TYPE_URI_LIST:
        uri = selection.get_data().strip(b'\r\n\x00')
        updatelog('uri = ', uri)
        uri_splitted = uri.split() # we may have more than one file dropped
        for uri in uri_splitted:
            path = urllib.parse.unquote(uri) 
            path = path.replace("file:///", "")
            path = path.replace("/", "\\")
            updatelog("path is " + path)
        gui.entry_set_text(path)
    return True


def os_exit(self):
    global player
    updatelog("Exit to OS.  release PIPELINE... quit GTK Main.....")
    player.pipeline.set_state(Gst.State.PAUSED)
    player.pipeline.set_state(Gst.State.NULL)
    Gtk.main_quit()

    

def do_args(args):
    global player, recorder, gui, osc, debug_gui
  
    if (args.preset == "test"):
        updatelog("Create player pipeline -- test mode")
        player = pipeline_test_source()
                
    elif (args.preset == "rtmp"):
        updatelog("Create player pipeline -- rtmp mode")
        player = pipeline_rtmp()
    elif (args.preset == "rtmp_dual"):
        updatelog("Create player pipeline -- rtmp dual mode")
        player = pipeline_rtmp_dual()
    else:
        updatelog("Create player pipeline -- decklink mode")
        player = pipeline_decklink_source()

    player.build_pipe()
    updatelog("Create recorder pipeline")
    recorder = bin_file_rec()
    
    player.clip = args.clip
    player.clip2 = args.clip2
     
    gui.entry_set_text(player.clip)         
    gui.move_primary(args.geometry_x, args.geometry_y)
    debug_gui.set_title("G Engine DebugWindow//  " + player.clip)
    ctypes.windll.kernel32.SetConsoleTitleW("G-Engine Decklink Source by sendust   //  " + args.clip)          ## Change console title
    
    if (args.preset == "test"):
        pass
    elif (args.preset == "rtmp"):              # setup preset specific parameters~~~
        player.set_rtmp_url(player.clip)
        player.set_rtmp_bitrate(args.bitrate)
        player.set_decklink_number(args.decklink)   # Setup decklink number
    elif (args.preset == "rtmp_dual"):              # setup preset specific parameters~~~
        player.set_rtmp_url(player.clip)
        player.set_rtmp_url2(player.clip2)
        player.set_rtmp_bitrate(args.bitrate)
        player.set_decklink_number(args.decklink)   # Setup decklink number
    else:
        player.set_decklink_number(args.decklink)   # Setup decklink number
        
    
    
    
    recorder.build_pipe()
    updatelog(vars(player))
    debug_gui.append_text(str(vars(player)))


def do_tcp_command(cmd):
    updatelog("TCP command {0} accepted".format(cmd))
    if cmd == "PLAY":
        pass
    if cmd == "PAUSE":
        pass
    if cmd[0:4] == "SEEK":
        position = float(cmd[5:])
        do_tcp_seek(position)       # Parse seek command
    
        

    
class cli_parser():
    def __init__(self):
        updatelog("Create argument parser")
        self.parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent('''\
        G_Engine decklink source Recorder. powered by sendust
          
          Preset list --------
             - test
             - rtmp
             - rtmp_dual'''))
        self.parser.add_argument("--clip", required=False, type=str, default="Test clip.mov", help="clip file to record / URL for rtmp streaming (rtmp://xxxx/yyy)")
        self.parser.add_argument("--clip2", required=False, type=str, default="rtmp://127.0.0.1/test", help="Second target for dual rtmp streaming (rtmp://yyyy/zzz)")
        self.parser.add_argument("--preset", required=False, type=str, default="", help="Load pipeline preset (Null or test)")
        self.parser.add_argument("--decklink",  required=False, type=int, default=0, help="Decklink number, start from 0 (default 0)")
        self.parser.add_argument("--port_report",  required=False, type=int, default=5253, help="UDP port for progress report (default 5253)")
        self.parser.add_argument("--port_command",  required=False, type=int, default=5250, help="TCP port for command reception (default 5250)")
        self.parser.add_argument("--geometry_x",  required=False, type=int, default=10, help="GUI Window start up X position")
        self.parser.add_argument("--geometry_y",  required=False, type=int, default=10, help="GUI Window start up Y position")
        self.parser.add_argument("--udp_osc_rate",  required=False, type=int, default=1, help="UDP report interval (default 1, Less report with large value)")
        self.parser.add_argument("--bitrate",  required=False, type=int, default=4000, help="bitrate for streaming (kbit/s)")

    
    def print_args(self):
        self.args = self.parser.parse_args()
        updatelog(self.args)
        do_args(self.args)


class tcp_svr():
    def __init__(self, address="0.0.0.0", port=5250):
        updatelog("Create tcp class with port ", port)
        
        while True:
            try:      # Check another Engine instance and kill it before Start script
                updatelog("Check another engine is running..........")
                for proc in process_iter():
                    for conns in proc.connections(kind = 'inet'):
                        if conns.laddr.port == port:
                            updatelog("Another engine instance found  {0}... send term signal [SIGTERM]".format(proc))
                            proc.send_signal(SIGTERM) # or SIGKILL
                            time.sleep(0.1)

                # create an INET, STREAMing socket
                self.serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # bind the socket to a public host, and a well-known port
                result1 = self.serversocket.bind((address, port))
                updatelog("amcp bind result is {0}".format(result1)) 
                # become a server socket
                result2 = self.serversocket.listen(5)
                updatelog("amcp listen result is {0}".format(result2))
                if not result1 and not result2:     # successfully bind and listen 
                    break
            except Exception as err:
                updatelog(err)
                updatelog("Error creating socket.... retry.... /// {0}".format(err))
                #sys.exit()
        
    
    def run_server(self):
        while True:
            updatelog("<<<<Begin TCP acception>>>>>")
            clientsocket, address = self.serversocket.accept()
            updatelog("Address {} client connected ".format(address), end="")
            self.reply_client(clientsocket)

        
    def reply_client(self, clientsocket):
        try:
            data = clientsocket.recv(1024).decode()
            updatelog("Message from client: {}   ".format(data))
            clientsocket.send(("tcp received  // " + data).encode())
            do_tcp_command(data)
        except:
            updatelog("Error while TCP receive or send")
            

def do_preview():
    global player, gui
    player.element["sink_gtk"].props.widget.set_size_request(400,224)
    gui.layout.put(player.element["sink_gtk"].get_property("widget"), 10, 100)
    

            

def develop_temp():
    processor_a = decklink_processor_audio(8)
    pipeline = Gst.Pipeline.new("player-pipeline")
    bin = processor_a.stereo_4()
    src1 =  Gst.ElementFactory.make("audiotestsrc")
    filter_a1 = caps_filter("filtera1", "audio/x-raw,channels=(int)2")
    src2 =  Gst.ElementFactory.make("audiotestsrc")
    filter_a2 = caps_filter("filtera2", "audio/x-raw,channels=(int)2")
    src3 =  Gst.ElementFactory.make("audiotestsrc")
    filter_a3 = caps_filter("filtera3", "audio/x-raw,channels=(int)2")
    src4 =  Gst.ElementFactory.make("audiotestsrc")
    filter_a4 = caps_filter("filtera4", "audio/x-raw,channels=(int)2")


    pipeline.add(bin)

    pipeline.add(src1)
    pipeline.add(filter_a1)
    pipeline.add(src2)
    pipeline.add(filter_a2)
    pipeline.add(src3)
    pipeline.add(filter_a3)
    pipeline.add(src4)
    pipeline.add(filter_a4)


    Gst.Element.link(src1, filter_a1)
    Gst.Element.link(filter_a1, bin)

    Gst.Element.link(src2, filter_a2)
    Gst.Element.link(filter_a2, bin)

    Gst.Element.link(src3, filter_a3)
    Gst.Element.link(filter_a3, bin)

    Gst.Element.link(src4, filter_a4)
    Gst.Element.link(filter_a4, bin)


    pipeline.set_state(Gst.State.PLAYING)

    time.sleep(2)


    Gst.debug_bin_to_dot_file(pipeline, Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "bintest")


    sys.exit(0)

def connect(src, pipeline, convert):
    updatelog(src)
    src.link(convert)
    
    
def develop_temp2():

    pipeline = Gst.Pipeline.new()
    src =  Gst.ElementFactory.make("filesrc")
    src.set_property("location", "F:/capture/스피드 IBC PGM_20180221-195303.mxf")
    #src.set_property("location", "S:/capture/c0052.mxf")
    decodebin = Gst.ElementFactory.make("decodebin")
    
    queue =  Gst.ElementFactory.make("queue")
    deinterlace =  Gst.ElementFactory.make("deinterlace")
    interlace =  Gst.ElementFactory.make("interlace")
    interlace.set_property("field-pattern", "1:1")
    
    convert = Gst.ElementFactory.make("videoconvert")
    sink =  Gst.ElementFactory.make("decklinkvideosink")
    sink.set_property("device-number", 1)
        
    pipeline.add(src)
    pipeline.add(decodebin)
    pipeline.add(convert)
    pipeline.add(sink)
    
    
    
    src.link(decodebin)
    convert.link(sink)
    
    decodebin.connect("no-more-pads", connect, pipeline, convert)
    pipeline.set_state(Gst.State.PAUSED)
    
    time.sleep(5)
    Gst.debug_bin_to_dot_file(pipeline, Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "bintest")
    sys.exit(0)

    
    
def develop_temp3():

    pipeline = Gst.parse_launch('filesrc location="s:/ahk/transcoder/osmo/DJI_0005.MOV" ! decodebin ! autovideoconvert ! autovideosink')
    #pipeline = Gst.parse_launch('videotestsrc ! autovideosink')
   

    
    pipeline.set_state(Gst.State.PAUSED)
    time.sleep(5)
    Gst.debug_bin_to_dot_file(pipeline, Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "bintest")    
    sys.exit(0)

    
#develop_temp2()

pid = os.getpid()    
gui = mywindow()
gui.move_primary(5, 5)
debug_gui = debugwindow()
debug_gui.show_gui()


args_list = cli_parser()
args_list.print_args()

osc = osc_like_udp_info("127.0.0.1", args_list.args.port_report)  # default 5253
osc.set_udp_interval(args_list.args.udp_osc_rate)                 # default 1
amcp = tcp_svr("127.0.0.1", args_list.args.port_command)          # default 5250

threading.Thread(target=amcp.run_server, daemon=True).start()  # Start amcp server with new thread (daemon enables termination of thread while script quiet)
#threading.Timer(1, lambda: gui.maximize()).start()  ## Restore minimized GTK window


try:
    updatelog("Start gui loop -----------------------")
    do_load()
    do_preview() 
    player.play()   # Start Decklink EE input monitor
    #debug_gui.hide_gui()
    gui.run()


except KeyboardInterrupt:
    os_exit("")
    sys.exit(0)
    
    

#############################################################################
#############################################################################
################  Ununsed function... class  ################################
#############################################################################
#############################################################################
#############################################################################


     
class bin_file_rec_backup():

    def __init__(self):
        self.bin = Gst.Bin.new("recbin")
        self.element = {}
        self.is_recording = False
        
    def no_more_pad_handler(self, src):  
        pass

    def build_pipe(self):
    
        updatelog("<<< build pipeline for decklink recorder >>>")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
               
        self.element["queue_v"] = Gst.ElementFactory.make("queue", "queue_v")
        self.element["queue_v"].set_property("flush-on-eos", True)   # For fast finializing
        self.element["convert_v"] = Gst.ElementFactory.make("videoconvert", "convert_v")
        self.element["encoder_v"] = Gst.ElementFactory.make("avenc_mpeg2video")
        self.element["encoder_v"].set_property("threads", 0)    # Set threads auto (0)
        self.element["mux"] = Gst.ElementFactory.make("mpegtsmux")
        self.element["filesink"] = Gst.ElementFactory.make("filesink")
        self.element["filesink"].set_property("location", "test2.ts")
        updatelog("add file sink buffer probe")
        #result = self.element["encoder_v"].get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.DATA_DOWNSTREAM, cb_test)
        result = self.element["encoder_v"].get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER , cb_rec)
        updatelog(result)
        
        for el in self.element:
            updatelog("Add element to pipeline " , el , self.bin.add(self.element[el]))

        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["queue_v"], self.element["convert_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["convert_v"], self.element["encoder_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["encoder_v"], self.element["mux"]), end="  ")
        updatelog(Gst.Element.link(self.element["mux"], self.element["filesink"]))

        updatelog("Finish manual element link ------------probetype--------------------------")

        updatelog("Create ghost pad")
        updatelog(self.bin.add_pad(Gst.GhostPad("sink", self.element["queue_v"].get_static_pad("sink"))))
        
        
    def set_filesink(self, fullpath):
        self.element["filesink"].set_property("location", fullpath)

    def get_bin(self):
        return self.bin
        

    
def do_rec_backup():           
    updatelog("Execute start rec")
    global player, recorder, gui
    recorder.frame_count = 0
    recorder.set_filesink("test" + time.strftime("%Y%m%d-%H%M%S") + ".ts")
    player.pipeline.add(recorder.get_bin())
    updatelog("connect tee with recorder bin")
    player.tee_v_src_pad.link(recorder.get_bin().get_static_pad("sink"))
    result = recorder.get_bin().set_state(Gst.State.PLAYING)
    if (result != Gst.StateChangeReturn.FAILURE):
        recorder.is_recording = True
        gui.button2.set_label("Stop REC")
    else:
        recorder.is_recording = False
        gui.button2.set_label("Start REC")
    