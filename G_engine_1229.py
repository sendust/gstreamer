#  Media Playback Engine Powered by Gstreamer
#  
#  Managed by sendust
#  Last Edit : 2022/3/17
#
#  bus message callback function
#  UDP status report
#  TCP server for transport control
#
#  uridecodebin 
#  add-pad callback function
#  
#  2021/12/3  Improve audio stream detection (audio channel)
#  Preset List :
#    - vga_anything
#    - decklink_hd_mxf
#    - decklink_hd_anything
#    - decklink_hd_interlaced   
#    - decklink_hd_anything_int                 for interlaced material, use decodebin
#    - decklink_hd_prores_mov
#    - decklink_4k_h264_mxf
#    - decklink_4k_prores
#    - audio_only
#
#  2021/12/20  Add auto_start argement (after load -> auto play)
#              in demuxer_pad class, add stream array
#  2021/12/27  Improve HD MXF audio (2, 4, 8ch), 4k prores audio (2, 8, 16ch)handling
#  2021/12/29  introduce various audio format
#  2022/1/4    Add new audio format for decklink (quad-1, quad-2)
#  2022/1/17   Add pad probe for seek load
#              Add port_command, port_report option parameter
#  2022/1/25   Add debug window, add file logging
#              Add setup_queue_size function (applied decklink_hd_mxf pipeline)
#  2022/2/25   Add audio_only profile (under dev....)
#  2022/2/28   Add Audio visual effect with gtk sink
#  2022/3/17   Add subprocess_run, waveform_draw class
#              Show waveform with progress bar (with audio_only preset)
#  2022/3/19   Add gui move_primary method
#  2022/3/21   Support decklink audio output while audio_only preset (Decklink video = spectrum image)
#  2022/3/24   Support wasapi audio sink. Add --guid option 
#  2022/4/23   Use default wasapisink if no guid.
#              adjustable udp report rate
#
#





import gi, threading, time, socket, argparse, ctypes, subprocess, signal
import os, urllib.parse, sys, textwrap, datetime
from psutil import process_iter
from signal import SIGTERM # or SIGKILL


gi.require_version('Gst', '1.0')
gi.require_version('Gtk', '3.0')
gi.require_version("GstAudio", "1.0")

from gi.repository import Gst, Gtk, GLib, Gdk, GstAudio



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


def setup_queue_size(queue, size_buffer = 2000, size_mbyte = 100, size_time = 5):
    queue.set_property("max-size-buffers", size_buffer)
    queue.set_property("max-size-bytes", size_mbyte * 1000000)
    queue.set_property("max-size-time", size_time * Gst.SECOND)


def updatelog(*args, end="\r\n"):    # Added 2022/1/25
    log_prefix = "G_Engine_log_"
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
    converter = Gst.ElementFactory.make("videoconvert")
    converter.set_property("n-threads", cpu_count)
    resize = Gst.ElementFactory.make("videoscale")
    resize.set_property("n-threads", cpu_count)
    rate = Gst.ElementFactory.make("videorate")
    caps_filter =  Gst.ElementFactory.make("capsfilter")
    caps_filter.set_property("caps", Gst.Caps.from_string(string_caps))
    
    setup_queue_size(queue, 2000, 100, 5)      # Create large queue size, added 2022/1/25
    
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



class decklink_processor_audio():       # Audio preprocessor for decklink audio output

    count_out = 8
    channel_filter = []
    
    def __init__(self, count_outch = 8):
        self.count_out = count_outch

        self.channel_filter = ["audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x01",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x02",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x04",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x08",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x10",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x20",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x40",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x80",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x100",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x200",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x400",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x800",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x1000",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x2000",
                                "audio/x-raw,channels=(int)1,channel-mask=(bitmask)0x8000"]
    
    
        self.channel_layout_4 = Gst.ValueArray([GstAudio.AudioChannelPosition.FRONT_LEFT,
                                                GstAudio.AudioChannelPosition.FRONT_RIGHT,
                                                GstAudio.AudioChannelPosition.REAR_LEFT,
                                                GstAudio.AudioChannelPosition.REAR_RIGHT])
        
        self.channel_layout_8 = Gst.ValueArray([GstAudio.AudioChannelPosition.FRONT_LEFT,
                                                GstAudio.AudioChannelPosition.FRONT_RIGHT,
                                                GstAudio.AudioChannelPosition.FRONT_CENTER,
                                                GstAudio.AudioChannelPosition.LFE1,
                                                GstAudio.AudioChannelPosition.REAR_LEFT,
                                                GstAudio.AudioChannelPosition.REAR_RIGHT,
                                                GstAudio.AudioChannelPosition.FRONT_LEFT_OF_CENTER,
                                                GstAudio.AudioChannelPosition.FRONT_RIGHT_OF_CENTER])
    

    def multi_channel(self, channel_nbr):
        bin = Gst.Bin.new("decklink_multichannel")
        queue = Gst.ElementFactory.make("queue")
        converter = Gst.ElementFactory.make("audioconvert")
        resample = Gst.ElementFactory.make("audioresample")
        caps_output = caps_filter("caps_output", "audio/x-raw,format=(string)S32LE,rate=48000,channels=(int)" + str(channel_nbr) + ",layout=(string)interleaved")
        
        bin.add(queue)
        bin.add(converter)
        bin.add(resample)
        bin.add(caps_output)
        
        updatelog("Create decklink multi channel audio bin")
        updatelog(Gst.Element.link(queue, converter), end="  ")
        updatelog(Gst.Element.link(converter, resample), end="  ")
        updatelog(Gst.Element.link(resample, caps_output), end="  ")
        
        bin.add_pad(Gst.GhostPad("sink", queue.get_static_pad("sink")))
        bin.add_pad(Gst.GhostPad("src", caps_output.get_static_pad("src")))
        
        
        return bin
        
    
    def mono_8(self):       # input mono-8 stream, output : interleaved 8ch stream
        bin = Gst.Bin.new("mono_8")
        queue = [None] * 8
        converter = [None] * 8
        caps_a = [None] * 8
        interleave = Gst.ElementFactory.make("audiointerleave")
        resample = Gst.ElementFactory.make("audioresample")
        caps_output = caps_filter("caps_output", "audio/x-raw,format=(string)S32LE,rate=48000,channels=(int)8,layout=(string)interleaved")
        converter_output = Gst.ElementFactory.make("audioconvert")
        
        bin.add(interleave)
        bin.add(caps_output)
        bin.add(resample)
        bin.add(converter_output)
        
        pad_template = interleave.get_pad_template("sink_%u")
        for i in range(8): # Make mono 8 channel processor
            queue[i] = Gst.ElementFactory.make("queue")
            converter[i] = Gst.ElementFactory.make("audioconvert")
            caps_a[i] =  caps_filter("ch" + str(i), self.channel_filter[i])
            pad_req = interleave.request_pad(pad_template)
            
            bin.add(queue[i])
            bin.add(converter[i])
            bin.add(caps_a[i])
            
            updatelog("mono_8 channel decklink audio processor.... link channel element .. ", i)
            updatelog(Gst.Element.link(queue[i], converter[i]), end="  ")
            updatelog(Gst.Element.link(converter[i], caps_a[i]), end="  ")
            updatelog(caps_a[i].link_pads("src", interleave, "sink_" + str(i)))

            bin.add_pad(Gst.GhostPad("sink_" + str(i), queue[i].get_static_pad("sink")))
        
        updatelog("Connect decklink output stage element")
        updatelog(Gst.Element.link(interleave, converter_output), end="  ")
        updatelog(Gst.Element.link(converter_output, resample), end="  ")
        updatelog(Gst.Element.link(resample, caps_output))
        
        bin.add_pad(Gst.GhostPad("src", caps_output.get_static_pad("src")))

        return bin



    def mono_4(self):       # input mono-8 stream, output : interleaved 8ch stream
        bin = Gst.Bin.new("mono_4")
        queue = [None] * 4
        converter = [None] * 4
        caps_a = [None] * 4
        interleave = Gst.ElementFactory.make("audiointerleave")
        resample = Gst.ElementFactory.make("audioresample")
        caps_output = caps_filter("caps_output", "audio/x-raw,format=(string)S32LE,rate=48000,channels=(int)8,layout=(string)interleaved")
        converter_output = Gst.ElementFactory.make("audioconvert")
        amatrix = audio_mix_matrix([[1,0,0,0],[0,1,0,0],[0,0,1,0,],[0,0,0,1],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]])
        
        
        bin.add(interleave)
        bin.add(amatrix)
        bin.add(caps_output)
        bin.add(resample)
        bin.add(converter_output)
        
     
        pad_template = interleave.get_pad_template("sink_%u")
        for i in range(4): # Make mono 8 channel processor
            queue[i] = Gst.ElementFactory.make("queue")
            converter[i] = Gst.ElementFactory.make("audioconvert")
            caps_a[i] =  caps_filter("ch" + str(i), self.channel_filter[i])
            pad_req = interleave.request_pad(pad_template)
            
            bin.add(queue[i])
            bin.add(converter[i])
            bin.add(caps_a[i])
            
            updatelog("mono_4 channel decklink audio processor.... link channel element .. ", i)
            updatelog(Gst.Element.link(queue[i], converter[i]), end="  ")
            updatelog(Gst.Element.link(converter[i], caps_a[i]), end="  ")
            updatelog(caps_a[i].link_pads("src", interleave, "sink_" + str(i)))
            
            bin.add_pad(Gst.GhostPad("sink_" + str(i), queue[i].get_static_pad("sink")))
              
        updatelog("Connect decklink output stage element")
        updatelog(Gst.Element.link(interleave, amatrix), end="  ")
        updatelog(Gst.Element.link(amatrix, converter_output), end="  ")
        updatelog(Gst.Element.link(converter_output, resample), end="  ")
        updatelog(Gst.Element.link(resample, caps_output))
        
        bin.add_pad(Gst.GhostPad("src", caps_output.get_static_pad("src")))

        return bin

    def mono_2(self):       # input mono-2 stream, output : interleaved 2ch stream
        bin = Gst.Bin.new("mono_2")
        queue = [None] * 2
        converter = [None] * 2
        caps_a = [None] * 2
        interleave = Gst.ElementFactory.make("audiointerleave")
        resample = Gst.ElementFactory.make("audioresample")
        caps_output = caps_filter("caps_output", "audio/x-raw,format=(string)S32LE,rate=48000,channels=(int)2,layout=(string)interleaved")
        converter_output = Gst.ElementFactory.make("audioconvert")
        
        bin.add(interleave)
        bin.add(caps_output)
        bin.add(resample)
        bin.add(converter_output)
        
        pad_template = interleave.get_pad_template("sink_%u")
        for i in range(2): # Make mono 8 channel processor
            queue[i] = Gst.ElementFactory.make("queue")
            converter[i] = Gst.ElementFactory.make("audioconvert")
            caps_a[i] =  caps_filter("ch" + str(i), self.channel_filter[i])
            pad_req = interleave.request_pad(pad_template)
            
            bin.add(queue[i])
            bin.add(converter[i])
            bin.add(caps_a[i])
            
            updatelog("mono_2 channel decklink audio processor.... link channel element .. ", i)
            updatelog(Gst.Element.link(queue[i], converter[i]), end="  ")
            updatelog(Gst.Element.link(converter[i], caps_a[i]), end="  ")
            updatelog(caps_a[i].link_pads("src", interleave, "sink_" + str(i)))
            
            bin.add_pad(Gst.GhostPad("sink_" + str(i), queue[i].get_static_pad("sink")))
        
        updatelog("Connect decklink output stage element")
        updatelog(Gst.Element.link(interleave, converter_output), end="  ")
        updatelog(Gst.Element.link(converter_output, resample), end="  ")
        updatelog(Gst.Element.link(resample, caps_output))
        
        bin.add_pad(Gst.GhostPad("src", caps_output.get_static_pad("src")))

        return bin



    def cb_stereo_4_0(self, element):
        name_element = element.get_name()
        updatelog("No more pad on " , name_element)
        self.deinterleave[0].get_static_pad("src_0").link(self.interleave.get_static_pad("sink_0"))
        self.deinterleave[0].get_static_pad("src_1").link(self.interleave.get_static_pad("sink_1"))
    
    def cb_stereo_4_1(self, element):
        name_element = element.get_name()
        updatelog("No more pad on " , name_element)
        self.deinterleave[1].get_static_pad("src_0").link(self.interleave.get_static_pad("sink_2"))
        self.deinterleave[1].get_static_pad("src_1").link(self.interleave.get_static_pad("sink_3"))
    
        
        
    
    def cb_stereo_4_2(self, element):
        name_element = element.get_name()
        updatelog("No more pad on " , name_element)
        self.deinterleave[2].get_static_pad("src_0").link(self.interleave.get_static_pad("sink_4"))
        self.deinterleave[2].get_static_pad("src_1").link(self.interleave.get_static_pad("sink_5"))
    
    
    def cb_stereo_4_3(self, element):
        name_element = element.get_name()
        updatelog("No more pad on " , name_element)
        self.deinterleave[3].get_static_pad("src_0").link(self.interleave.get_static_pad("sink_6"))
        self.deinterleave[3].get_static_pad("src_1").link(self.interleave.get_static_pad("sink_7"))
    
            
    def stereo_4(self):
        self.bin = Gst.Bin.new("stereo_4")
        self.queue =  [None] * 4
        self.deinterleave = [None] * 4
        self.interleave = self.mono_8()
        
        self.bin.add(self.interleave)

      
        for i in range(4):
            self.queue[i] = Gst.ElementFactory.make("queue")
            self.deinterleave[i] = Gst.ElementFactory.make("deinterleave")
         
            self.bin.add(self.queue[i])
            self.bin.add(self.deinterleave[i])
            
            updatelog("stereo_4 channel decklink audio processor.... link channel element .. ", i)
            updatelog(Gst.Element.link(self.queue[i], self.deinterleave[i]))
        
            self.bin.add_pad(Gst.GhostPad("sink_" + str(i), self.queue[i].get_static_pad("sink")))

        self.deinterleave[0].connect("no-more-pads", self.cb_stereo_4_0)
        self.deinterleave[1].connect("no-more-pads", self.cb_stereo_4_1) 
        self.deinterleave[2].connect("no-more-pads", self.cb_stereo_4_2)  
        self.deinterleave[3].connect("no-more-pads", self.cb_stereo_4_3)  
        
        self.bin.add_pad(Gst.GhostPad("src", self.interleave.get_static_pad("src")))
        return self.bin

    def stereo_2(self):
        self.bin = Gst.Bin.new("stereo_2")
        self.queue =  [None] * 2
        self.deinterleave = [None] * 2
        self.interleave = self.mono_4()
        
        self.bin.add(self.interleave)

      
        for i in range(2):
            self.queue[i] = Gst.ElementFactory.make("queue")
            self.deinterleave[i] = Gst.ElementFactory.make("deinterleave")
         
            self.bin.add(self.queue[i])
            self.bin.add(self.deinterleave[i])
            
            updatelog("stereo_4 channel decklink audio processor.... link channel element .. ", i)
            updatelog(Gst.Element.link(self.queue[i], self.deinterleave[i]))
        
            self.bin.add_pad(Gst.GhostPad("sink_" + str(i), self.queue[i].get_static_pad("sink")))

        self.deinterleave[0].connect("no-more-pads", self.cb_stereo_4_0)
        self.deinterleave[1].connect("no-more-pads", self.cb_stereo_4_1) 
        
        self.bin.add_pad(Gst.GhostPad("src", self.interleave.get_static_pad("src")))
        return self.bin

    
    
    def stereo_1(self):
        self.bin = Gst.Bin.new("stereo_1")
        queue =  Gst.ElementFactory.make("queue")
        resample = Gst.ElementFactory.make("audioresample")
        converter_output = Gst.ElementFactory.make("audioconvert")
        caps_output = caps_filter("caps_output", "audio/x-raw,format=(string)S32LE,rate=48000,channels=(int)2,layout=(string)interleaved")

        self.bin.add(queue)
        self.bin.add(resample)
        self.bin.add(converter_output)
        self.bin.add(caps_output)

        queue.link(resample)
        resample.link(converter_output)
        converter_output.link(caps_output)
        
        self.bin.add_pad(Gst.GhostPad("sink", queue.get_static_pad("sink")))
        self.bin.add_pad(Gst.GhostPad("src", caps_output.get_static_pad("src")))
        return self.bin


    def cb_quad_2_0(self, element):
        name_element = element.get_name()
        updatelog("No more pad on " , name_element)
        self.deinterleave[0].get_static_pad("src_0").link(self.interleave.get_static_pad("sink_0"))
        self.deinterleave[0].get_static_pad("src_1").link(self.interleave.get_static_pad("sink_1"))
        self.deinterleave[0].get_static_pad("src_2").link(self.interleave.get_static_pad("sink_2"))
        self.deinterleave[0].get_static_pad("src_3").link(self.interleave.get_static_pad("sink_3"))
    
    def cb_quad_2_1(self, element):
        name_element = element.get_name()
        updatelog("No more pad on " , name_element)
        self.deinterleave[1].get_static_pad("src_0").link(self.interleave.get_static_pad("sink_4"))
        self.deinterleave[1].get_static_pad("src_1").link(self.interleave.get_static_pad("sink_5"))
        self.deinterleave[1].get_static_pad("src_2").link(self.interleave.get_static_pad("sink_6"))
        self.deinterleave[1].get_static_pad("src_3").link(self.interleave.get_static_pad("sink_7"))
        

    def quad_2(self):
        self.bin = Gst.Bin.new("quad_2")
        self.queue =  [None] * 2
        self.deinterleave = [None] * 2
        self.interleave = self.mono_8()
        
        self.bin.add(self.interleave)

      
        for i in range(2):
            self.queue[i] = Gst.ElementFactory.make("queue")
            self.deinterleave[i] = Gst.ElementFactory.make("deinterleave")
         
            self.bin.add(self.queue[i])
            self.bin.add(self.deinterleave[i])
            
            updatelog("quad_2 channel decklink audio processor.... link channel element .. ", i)
            updatelog(Gst.Element.link(self.queue[i], self.deinterleave[i]))
        
            self.bin.add_pad(Gst.GhostPad("sink_" + str(i), self.queue[i].get_static_pad("sink")))

        self.deinterleave[0].connect("no-more-pads", self.cb_quad_2_0)
        self.deinterleave[1].connect("no-more-pads", self.cb_quad_2_1) 
        
        self.bin.add_pad(Gst.GhostPad("src", self.interleave.get_static_pad("src")))
        return self.bin

    def quad_1(self):

        self.bin = Gst.Bin.new("quad_1")
        queue = Gst.ElementFactory.make("queue")
        resample = Gst.ElementFactory.make("audioresample")
        converter_output = Gst.ElementFactory.make("audioconvert")
        caps_output = caps_filter("caps_output", "audio/x-raw,format=(string)S32LE,rate=48000,channels=(int)4,layout=(string)interleaved")
        amatrix = audio_mix_matrix([[1,0,0,0],[0,1,0,0],[0,0,1,0,],[0,0,0,1],[0,0,0,0],[0,0,0,0],[0,0,0,0],[0,0,0,0]])

        self.bin.add(queue)
        self.bin.add(resample)
        self.bin.add(converter_output)
        self.bin.add(caps_output)
        self.bin.add(amatrix)

        updatelog(Gst.Element.link(queue, resample), end=" ")
        updatelog(Gst.Element.link(resample, converter_output), end=" ")
        updatelog(Gst.Element.link(converter_output, caps_output), end="  ")
        updatelog(Gst.Element.link(caps_output, amatrix))

        self.bin.add_pad(Gst.GhostPad("sink", queue.get_static_pad("sink")))
        self.bin.add_pad(Gst.GhostPad("src", amatrix.get_static_pad("src")))
        
        return self.bin


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
        rms_value = []          # no audio stream

    for temp in range(8):           # fill up with 0 
        rms_value.append(-700)
        osc.volume[temp] = rms_value[temp]  # Setup osc volume data from level message

    end_time =  structure_message.get_value("endtime")
    timestamp =  structure_message.get_value("timestamp")
    stream_time =  structure_message.get_value("stream-time")
    running_time =  structure_message.get_value("running-time")
    duration_time =  structure_message.get_value("duration")
    #print(rms_value)
    #print(" end={0}, timestamep={1}, stream={2}, run={3}, duration={4}, level is {5}".format(end_time, timestamp, stream_time, running_time, duration_time, rms_value), end="\r")
    

    
# Audio processor for vga_anything preset
    
def do_audio_vga(this, name_src_element , name_sink_element): 
    if len(this.demuxer_pads.audio):            # Check if there is audio pad
        this.element[name_src_element].link_pads(this.demuxer_pads.audio[0], this.element[name_sink_element], "sink")
        updatelog("Connect audio [src element] and [sink element] ", name_src_element, "   <----->   " , name_sink_element)
        updatelog("Connect audio demuxer pad " , this.demuxer_pads.audio[0])
    else:
        this.pipeline.remove(this.element["sink_a"])        # Remove audio sink device

# Audio processor for decklink preset
def do_audio_decklink(this, name_src_element, name_sink_element): 

    decklink_audio = decklink_processor_audio()
    
    updatelog("multi channel audio layout is " , this.demuxer_pads.audio_many_channel)
    
    if (len(this.demuxer_pads.audio_mono) == 8):            # for 8 channel mxf
        this.element["decklink_audio_bin"] = decklink_audio.mono_8()
        this.pipeline.add(this.element["decklink_audio_bin"])
        # Important !!! Syncronize custom bin state with player-pipeline
        this.element["decklink_audio_bin"].sync_state_with_parent()
        
        for i in range(8):
            pad_sink_decklink_audio = this.element["decklink_audio_bin"].get_static_pad("sink_" + str(i))
            pad_src_demux_audio = this.element[name_src_element].get_static_pad(this.demuxer_pads.audio_mono[i])
            pad_src_demux_audio.link(pad_sink_decklink_audio)   
        
    elif (len(this.demuxer_pads.audio_mono) == 4):           # for 4 channel mxf
        this.element["decklink_audio_bin"] = decklink_audio.mono_4()
        this.pipeline.add(this.element["decklink_audio_bin"])
        # Important !!! Syncronize custom bin state with player-pipeline
        this.element["decklink_audio_bin"].sync_state_with_parent()
        
        for i in range(4):
            pad_sink_decklink_audio = this.element["decklink_audio_bin"].get_static_pad("sink_" + str(i))
            pad_src_demux_audio = this.element[name_src_element].get_static_pad(this.demuxer_pads.audio_mono[i])
            pad_src_demux_audio.link(pad_sink_decklink_audio)        

    elif (len(this.demuxer_pads.audio_mono) == 2):           # for 2 channel mxf
        this.element["decklink_audio_bin"] = decklink_audio.mono_2()
        this.pipeline.add(this.element["decklink_audio_bin"])
        # Important !!! Syncronize custom bin state with player-pipeline
        this.element["decklink_audio_bin"].sync_state_with_parent()
        
        for i in range(2):
            pad_sink_decklink_audio = this.element["decklink_audio_bin"].get_static_pad("sink_" + str(i))
            pad_src_demux_audio = this.element[name_src_element].get_static_pad(this.demuxer_pads.audio_mono[i])
            pad_src_demux_audio.link(pad_sink_decklink_audio)    
        
    elif ((len(this.demuxer_pads.audio_mono) == 1) | (len(this.demuxer_pads.audio_stereo) == 1)): # mono or stereo 1 stream
        this.element["decklink_audio_bin"] = decklink_audio.stereo_1()
        this.pipeline.add(this.element["decklink_audio_bin"])
        # Important !!! Syncronize custom bin state with player-pipeline
        this.element["decklink_audio_bin"].sync_state_with_parent()
        
        pad_sink_decklink_audio = this.element["decklink_audio_bin"].get_static_pad("sink")
        pad_src_demux_audio = this.element[name_src_element].get_static_pad(this.demuxer_pads.audio[0])
        pad_src_demux_audio.link(pad_sink_decklink_audio)
    
    elif (len(this.demuxer_pads.audio_stereo) == 4):
        this.element["decklink_audio_bin"] = decklink_audio.stereo_4()
        this.pipeline.add(this.element["decklink_audio_bin"])
        # Important !!! Syncronize custom bin state with player-pipeline
        this.element["decklink_audio_bin"].sync_state_with_parent()
        
        for i in range(4):
            pad_sink_decklink_audio = this.element["decklink_audio_bin"].get_static_pad("sink_" + str(i))
            pad_src_demux_audio = this.element[name_src_element].get_static_pad(this.demuxer_pads.audio_stereo[i])
            pad_src_demux_audio.link(pad_sink_decklink_audio)         

    elif (len(this.demuxer_pads.audio_stereo) == 2):
        this.element["decklink_audio_bin"] = decklink_audio.stereo_2()
        this.pipeline.add(this.element["decklink_audio_bin"])
        # Important !!! Syncronize custom bin state with player-pipeline
        this.element["decklink_audio_bin"].sync_state_with_parent()
        
        for i in range(2):
            pad_sink_decklink_audio = this.element["decklink_audio_bin"].get_static_pad("sink_" + str(i))
            pad_src_demux_audio = this.element[name_src_element].get_static_pad(this.demuxer_pads.audio_stereo[i])
            pad_src_demux_audio.link(pad_sink_decklink_audio)      

    elif ((len(this.demuxer_pads.audio_multi) == 2) and (this.demuxer_pads.audio_many_channel == 4)): #  4 channel - 2 stream
        this.element["decklink_audio_bin"] = decklink_audio.quad_2()
        this.pipeline.add(this.element["decklink_audio_bin"])
        # Important !!! Syncronize custom bin state with player-pipeline
        this.element["decklink_audio_bin"].sync_state_with_parent()
        
        for i in range(2):
            pad_sink_decklink_audio = this.element["decklink_audio_bin"].get_static_pad("sink_" + str(i))
            pad_src_demux_audio = this.element[name_src_element].get_static_pad(this.demuxer_pads.audio_multi[i])
            pad_src_demux_audio.link(pad_sink_decklink_audio) 


    elif ((this.demuxer_pads.audio_many_channel == 16) | (this.demuxer_pads.audio_many_channel == 8)):   # 8, 16 channel audio
        this.element["decklink_audio_bin"] = decklink_audio.multi_channel(this.demuxer_pads.audio_many_channel)
        this.pipeline.add(this.element["decklink_audio_bin"])
        this.element["decklink_audio_bin"].sync_state_with_parent()
        
        pad_sink_decklink_audio = this.element["decklink_audio_bin"].get_static_pad("sink")
        pad_src_demux_audio = this.element[name_src_element].get_static_pad(this.demuxer_pads.audio_multi[0])
        pad_src_demux_audio.link(pad_sink_decklink_audio)   
    

    elif ((len(this.demuxer_pads.audio_multi) == 1) and (this.demuxer_pads.audio_many_channel == 4)):    #  quad stream 1
        this.element["decklink_audio_bin"] = decklink_audio.quad_1()
        this.pipeline.add(this.element["decklink_audio_bin"])
        # Important !!! Syncronize custom bin state with player-pipeline
        this.element["decklink_audio_bin"].sync_state_with_parent()
        
        pad_sink_decklink_audio = this.element["decklink_audio_bin"].get_static_pad("sink")
        pad_src_demux_audio = this.element[name_src_element].get_static_pad(this.demuxer_pads.audio_multi[0])
        updatelog(pad_src_demux_audio.link(pad_sink_decklink_audio))
    

    else:
        this.pipeline.remove(this.element["sink_a"])
        #this.element["sink_v"].set_clock(Gst.Clock)    
    
    try:
        updatelog(Gst.Element.link(this.element["decklink_audio_bin"], this.element[name_sink_element]))    
    except:
        updatelog("  >>>  Error connecting decklink_audio_bin <--> " , name_sink_element)
        


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
        print("sync message received " + str(self.count), end="\r")
        pass        


    def load(self, file_source):
        updatelog("Load with file %s" % file_source)
        
        try:
            self.element["source"].set_property("location", file_source)
        except:
            updatelog("Error while setup demuxer source file location")

        updatelog("Starat osc timer")
        GLib.timeout_add(50, do_osc)    # the time between calls to the function, in milliseconds (1/1000ths of a second)

        #starting up a timer to check on the current playback value (Move from PLAY to LOAD)
        GLib.timeout_add(500, self.update_position)        
        

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

    

class pipeline_vga_anything(myplayer):

    def no_more_pad_handler(self, src):  # mxf demuxer, final stage
        updatelog("There is no more pad on demuxer.... print video and audio pads")
        updatelog("Print all AV stream pads ", self.demuxer_pads.video, self.demuxer_pads.audio)
        for i, c in enumerate(self.demuxer_pads.stream[0]):   
            if c.isdigit():
                updatelog("found first number from demuxer pad name string [location] ", i)
                break
        self.demuxer_pads.audio.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_mono.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_stereo.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        updatelog("Stream list : " , self.demuxer_pads.stream)
        updatelog("Video stream list : " , self.demuxer_pads.video)
        updatelog("Audio stream list : " , self.demuxer_pads.audio)
        updatelog("Audio(mono)   stream list : " , self.demuxer_pads.audio_mono)
        updatelog("Audio(stereo) stream list : " , self.demuxer_pads.audio_stereo)
        updatelog("Audio(multi)  stream list : " , self.demuxer_pads.audio_multi)
       
        
        self.start_bus_message()
        

        if len(self.demuxer_pads.video):            # Check if there is video pad
            self.element["decode"].link_pads(self.demuxer_pads.video[0], self.element["tee"], "sink")
        else:
            self.element["sink_v"].set_state(Gst.State.NULL)
            self.pipeline.remove(self.element["sink_v"])        # Remove video sink device

        do_audio_vga(self, "decode", "queue_a")            # connect audio element
        
           
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "pipe_after_file_load")
        

    def build_pipe(self):
        updatelog("build pipeline")
        
        self.element["source"] = Gst.ElementFactory.make("filesrc")               
        self.element["decode"] = Gst.ElementFactory.make("decodebin", "decode")               
        self.element["queue_a"] = Gst.ElementFactory.make("queue", "queue_a")
        self.element["queue_v"] = Gst.ElementFactory.make("queue", "queue_v")
        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        self.element["sink_a"] =  Gst.ElementFactory.make("autoaudiosink", "sink_a0")
        self.element["sink_v"] =  Gst.ElementFactory.make("glimagesink", "sink_v0")
        self.element["audio_resample"] =  Gst.ElementFactory.make("audioresample", "audio_resample0")
        self.element["video_convert"] =  Gst.ElementFactory.make("videoconvert", "video_convert0") 
        self.element["audio_convert"] =  Gst.ElementFactory.make("audioconvert", "audio_convert0") 
        
        self.element["tee"] =  Gst.ElementFactory.make("tee")
        self.element["tee"].set_property("allow-not-linked", True)
        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", False)
        self.element["previewbin"] = queue_video_converter_bin("previewbin", "video/x-raw, width=160, height=90")
        self.element["previewbin"].sync_state_with_parent()
      
        self.element["level"].set_property("interval", 0.05 * Gst.SECOND)

        for el in self.element:
            updatelog("Add element to pipeline " , el , self.pipeline.add(self.element[el]))
            

        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source"], self.element["decode"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["tee"], self.element["queue_v"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["queue_v"], self.element["video_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert"], self.element["sink_v"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["tee"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")

        updatelog(Gst.Element.link(self.element["queue_a"], self.element["audio_resample"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_resample"], self.element["audio_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_convert"], self.element["level"]), end="  ")
        updatelog(Gst.Element.link(self.element["level"], self.element["sink_a"]))
        updatelog("Finish manual element link --------------------------------------")    
        
        
        self.element["decode"].connect("pad-added", self.pad_added_handler)
        self.element["decode"].connect("no-more-pads", self.no_more_pad_handler)
        
        do_preview()
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "pipe_before_file_load")

        
    def register_cb_seek(self):
        self.element["tee"].get_static_pad("src_0").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BLOCK, cb_seek_load)



class pipeline_audio_only_decklinkoutput(myplayer):        # Audio output : decklink
    # based on 
    # gst-launch-1.0 filesrc location=test.mp3 ! decodebin ! tee name=t ! queue ! audioconvert ! audioresample ! autoaudiosink t. ! queue ! audioconvert ! audioresample ! spectrascope ! videoconvert ! videoscale ! videorate ! video/x-raw, width=160, height=90 ! autovideosink
    # Modified from origin for decklink output (SD video, AES audio)
    

    def no_more_pad_handler(self, src):  # mxf demuxer, final stage
        updatelog("There is no more pad on demuxer.... print video and audio pads")
        updatelog("Print all AV stream pads ", self.demuxer_pads.video, self.demuxer_pads.audio)
        for i, c in enumerate(self.demuxer_pads.stream[0]):   
            if c.isdigit():
                updatelog("found first number from demuxer pad name string [location] ", i)
                break
        self.demuxer_pads.audio.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_mono.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_stereo.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        updatelog("Stream list : " , self.demuxer_pads.stream)
        updatelog("Video stream list : " , self.demuxer_pads.video)
        updatelog("Audio stream list : " , self.demuxer_pads.audio)
        updatelog("Audio(mono)   stream list : " , self.demuxer_pads.audio_mono)
        updatelog("Audio(stereo) stream list : " , self.demuxer_pads.audio_stereo)
        updatelog("Audio(multi)  stream list : " , self.demuxer_pads.audio_multi)
               
        self.start_bus_message()

        do_audio_vga(self, "decode", "tee")            # connect audio element
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "pipe_after_file_load")
        

    def build_pipe(self):
        updatelog("build pipeline")
        
        self.element["source"] = Gst.ElementFactory.make("filesrc")               
        self.element["decode"] = Gst.ElementFactory.make("decodebin", "decode")               
        self.element["queue_a"] = Gst.ElementFactory.make("queue", "queue_a")
        #setup_queue_size(self.element["queue_a"], 20000, 100, 10)
        
        self.element["queue_a_vis"] = Gst.ElementFactory.make("queue", "queue_a_vis")
        #setup_queue_size(self.element["queue_a_vis"], 20000, 100, 10)
        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        
        #self.element["sink_a"] =  Gst.ElementFactory.make("autoaudiosink", "sink_a0")
        self.element["sink_a"] =  Gst.ElementFactory.make("decklinkaudiosink", "sink_a0")
        self.element["sink_a"].set_property("device-number", self.decklink)   
        
        self.element["audio_resample"] =  Gst.ElementFactory.make("audioresample", "audio_resample0")
        self.element["audio_convert"] =  Gst.ElementFactory.make("audioconvert", "audio_convert0")
        
        
        self.element["queue_a_decklink"] = Gst.ElementFactory.make("queue", "queue_a_decklink")
        #setup_queue_size(self.element["queue_a_decklink"], 20000, 100, 10)
        self.element["audio_resample_decklink"] =  Gst.ElementFactory.make("audioresample", "audio_resample1")
        self.element["audio_convert_decklink"] =  Gst.ElementFactory.make("audioconvert", "audio_convert1")
        self.element["cap_a_decklink"] = caps_filter("caps_a_decklink", "audio/x-raw,format=(string)S32LE,rate=48000,channels=(int)2,layout=(string)interleaved")
        #self.element["cap_a_decklink"] = caps_filter("caps_a_decklink", "audio/x-raw")
        
        self.element["audio_resample_vis"] =  Gst.ElementFactory.make("audioresample", "audio_resample_vis")
        self.element["audio_convert_vis"] =  Gst.ElementFactory.make("audioconvert", "audio_convert_vis")         
        self.element["video_convert_vis"] =  Gst.ElementFactory.make("videoconvert", "video_convert_vis")         
        self.element["video_scale_vis"] =  Gst.ElementFactory.make("videoscale", "video_scale_vis")         
        self.element["video_rate_vis"] =  Gst.ElementFactory.make("videorate", "video_rate_vis")         
        
        self.element["tee"] =  Gst.ElementFactory.make("tee", "tee1")
        self.element["tee"].set_property("allow-not-linked", True)
        self.element["tee2"] =  Gst.ElementFactory.make("tee", "tee2")
        self.element["tee2"].set_property("allow-not-linked", True)
        self.element["queue_tee2_1"] = Gst.ElementFactory.make("queue", "queue_tee2_1")
        self.element["queue_tee2_2"] = Gst.ElementFactory.make("queue", "queue_tee2_2")
        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", True)

        self.element["caps_v_gtk"] = caps_filter("caps_v_gtk", "video/x-raw, width=160, height=90")
        self.element["audio_visual"] =  Gst.ElementFactory.make("spectrascope")
        #self.element["audio_visual"].set_property("shader", "fade-and-move-horiz-out")         # Do not activate this line  (gstreamer unstable !!!!, 2022/2/28)
   
   
        self.element["level"].set_property("interval", 0.05 * Gst.SECOND)


        # video sink for decklink video
        self.element["video_convert_decklink"] = Gst.ElementFactory.make("videoconvert", "video_convert_decklink") 
        self.element["video_scale_decklink"] = Gst.ElementFactory.make("videoscale", "video_scale_decklink")  
        self.element["video_rate_decklink"] = Gst.ElementFactory.make("videorate", "video_rate_decklink")             
        self.element["caps_v_decklink"] = caps_filter("cap_v_declink0", "video/x-raw,format=UYVY,width=720,height=486,framerate=30000/1001")
        #self.element["caps_v_decklink"] = caps_filter("caps_v_declink0", "video/x-raw,format=RGBA,framerate=30000/1001,width=720,height=486")
        self.element["queue_v_decklink"] = Gst.ElementFactory.make("queue", "queue_v_decklink")    
        #self.element["sink_v_decklink"] =  Gst.ElementFactory.make("autovideosink", "sink_v_decklink0")
        self.element["sink_v_decklink"] =  Gst.ElementFactory.make("decklinkvideosink", "sink_v_decklink0")
        self.element["sink_v_decklink"].set_property("device-number", self.decklink)      
        self.element["sink_v_decklink"].set_property("mode", "ntsc")      
        #self.element["sink_v_decklink"].set_property("max-lateness", -1)  


        for el in self.element:
            updatelog("Add element to pipeline " , el , self.pipeline.add(self.element[el]))
            

        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source"], self.element["decode"]), end="  ")

        updatelog(Gst.Element.link(self.element["tee"], self.element["queue_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_a"], self.element["audio_resample"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_resample"], self.element["audio_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_convert"], self.element["level"]), end="  ")
        updatelog(Gst.Element.link(self.element["level"], self.element["queue_a_decklink"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_a_decklink"], self.element["audio_resample_decklink"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_resample_decklink"], self.element["audio_convert_decklink"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_convert_decklink"], self.element["cap_a_decklink"]), end="  ")
        updatelog(Gst.Element.link(self.element["cap_a_decklink"], self.element["sink_a"]))
        
        updatelog(Gst.Element.link(self.element["tee"], self.element["queue_a_vis"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_a_vis"], self.element["audio_resample_vis"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_resample_vis"], self.element["audio_convert_vis"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_convert_vis"], self.element["audio_visual"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_visual"], self.element["tee2"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee2"], self.element["queue_tee2_1"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_tee2_1"], self.element["video_convert_vis"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert_vis"], self.element["video_scale_vis"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_scale_vis"], self.element["video_rate_vis"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_rate_vis"], self.element["caps_v_gtk"]), end="  ")
        updatelog(Gst.Element.link(self.element["caps_v_gtk"], self.element["sink_gtk"]))


        updatelog(Gst.Element.link(self.element["tee2"], self.element["queue_tee2_2"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_tee2_2"], self.element["video_convert_decklink"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert_decklink"], self.element["video_scale_decklink"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_scale_decklink"], self.element["video_rate_decklink"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_rate_decklink"], self.element["caps_v_decklink"]), end="  ")
        updatelog(Gst.Element.link(self.element["caps_v_decklink"], self.element["queue_v_decklink"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v_decklink"], self.element["sink_v_decklink"]))

        
        updatelog("Finish manual element link --------------------------------------")    
        
        
        self.element["decode"].connect("pad-added", self.pad_added_handler)
        self.element["decode"].connect("no-more-pads", self.no_more_pad_handler)
        
        do_preview()
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "pipe_before_file_load")

        
    def register_cb_seek(self):
        self.element["tee"].get_static_pad("src_0").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BLOCK, cb_seek_load)



class pipeline_audio_only(myplayer):      # Audio output : wasapisink
    # based on 
    # gst-launch-1.0 filesrc location=test.mp3 ! decodebin ! tee name=t ! queue ! audioconvert ! audioresample ! autoaudiosink t. ! queue ! audioconvert ! audioresample ! spectrascope ! videoconvert ! videoscale ! videorate ! video/x-raw, width=160, height=90 ! autovideosink

    def no_more_pad_handler(self, src):  # mxf demuxer, final stage
        updatelog("There is no more pad on demuxer.... print video and audio pads")
        updatelog("Print all AV stream pads ", self.demuxer_pads.video, self.demuxer_pads.audio)
        for i, c in enumerate(self.demuxer_pads.stream[0]):   
            if c.isdigit():
                updatelog("found first number from demuxer pad name string [location] ", i)
                break
        self.demuxer_pads.audio.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_mono.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_stereo.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        updatelog("Stream list : " , self.demuxer_pads.stream)
        updatelog("Video stream list : " , self.demuxer_pads.video)
        updatelog("Audio stream list : " , self.demuxer_pads.audio)
        updatelog("Audio(mono)   stream list : " , self.demuxer_pads.audio_mono)
        updatelog("Audio(stereo) stream list : " , self.demuxer_pads.audio_stereo)
        updatelog("Audio(multi)  stream list : " , self.demuxer_pads.audio_multi)
               
        self.start_bus_message()

        do_audio_vga(self, "decode", "tee")            # connect audio element
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "pipe_after_file_load")
        

    def build_pipe(self):
        updatelog("build pipeline")
        
        self.element["source"] = Gst.ElementFactory.make("filesrc")               
        self.element["decode"] = Gst.ElementFactory.make("decodebin", "decode")               
        self.element["queue_a"] = Gst.ElementFactory.make("queue", "queue_a")
        self.element["queue_a_vis"] = Gst.ElementFactory.make("queue", "queue_a_vis")
        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        self.element["sink_a"] =  Gst.ElementFactory.make("wasapisink", "sink_a0")
        
        if len(self.guid):
            updatelog("Set device guid " , self.guid)
            self.element["sink_a"].set_property("device", self.guid)
        else:
            updatelog("guid not provided. Use default wasapisink ")
        
        self.element["audio_resample"] =  Gst.ElementFactory.make("audioresample", "audio_resample0")
        self.element["audio_convert"] =  Gst.ElementFactory.make("audioconvert", "audio_convert0")
        
        self.element["audio_resample_vis"] =  Gst.ElementFactory.make("audioresample", "audio_resample_vis")
        self.element["audio_convert_vis"] =  Gst.ElementFactory.make("audioconvert", "audio_convert_vis")         
        self.element["video_convert_vis"] =  Gst.ElementFactory.make("videoconvert", "video_convert_vis")         
        self.element["video_scale_vis"] =  Gst.ElementFactory.make("videoscale", "video_scale_vis")         
        self.element["video_rate_vis"] =  Gst.ElementFactory.make("videorate", "video_rate_vis")         
        
        self.element["tee"] =  Gst.ElementFactory.make("tee")
        self.element["tee"].set_property("allow-not-linked", True)
        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", False)

        self.element["cap_v"] = caps_filter("caps_v0", "video/x-raw, width=160, height=90")
        self.element["audio_visual"] =  Gst.ElementFactory.make("spectrascope") 
        #self.element["audio_visual"].set_property("shader", "fade-and-move-horiz-out")         # Do not activate this line  (gstreamer unstable !!!!, 2022/2/28)
   
   
        self.element["level"].set_property("interval", 0.05 * Gst.SECOND)

        for el in self.element:
            updatelog("Add element to pipeline " , el , self.pipeline.add(self.element[el]))
            

        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source"], self.element["decode"]), end="  ")

        updatelog(Gst.Element.link(self.element["tee"], self.element["queue_a"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_a"], self.element["audio_resample"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_resample"], self.element["audio_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_convert"], self.element["level"]), end="  ")
        updatelog(Gst.Element.link(self.element["level"], self.element["sink_a"]))

        updatelog(Gst.Element.link(self.element["tee"], self.element["queue_a_vis"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_a_vis"], self.element["audio_resample_vis"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_resample_vis"], self.element["audio_convert_vis"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_convert_vis"], self.element["audio_visual"]), end="  ")
        updatelog(Gst.Element.link(self.element["audio_visual"], self.element["video_convert_vis"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert_vis"], self.element["video_scale_vis"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_scale_vis"], self.element["video_rate_vis"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_rate_vis"], self.element["cap_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["cap_v"], self.element["sink_gtk"]))
        
        
        updatelog("Finish manual element link --------------------------------------")    
        
        
        self.element["decode"].connect("pad-added", self.pad_added_handler)
        self.element["decode"].connect("no-more-pads", self.no_more_pad_handler)
        
        do_preview()
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "pipe_before_file_load")

        
    def register_cb_seek(self):
        self.element["tee"].get_static_pad("src_0").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BLOCK, cb_seek_load)

    
         

        
class pipeline_decklink_anything(myplayer):

    def no_more_pad_handler(self, src):  # mxf demuxer, final stage
        updatelog("There is no more pad on demuxer.... print video and audio pads")
        updatelog("Print all AV stream pads ", self.demuxer_pads.video, self.demuxer_pads.audio)
        for i, c in enumerate(self.demuxer_pads.stream[0]):   
            if c.isdigit():
                updatelog("found first number from demuxer pad name string [location] ", i)
                break
        self.demuxer_pads.audio.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_mono.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_stereo.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        updatelog("Stream list : " , self.demuxer_pads.stream)
        updatelog("Video stream list : " , self.demuxer_pads.video)
        updatelog("Audio stream list : " , self.demuxer_pads.audio)
        updatelog("Audio(mono)   stream list : " , self.demuxer_pads.audio_mono)
        updatelog("Audio(stereo) stream list : " , self.demuxer_pads.audio_stereo)
        updatelog("Audio(multi)  stream list : " , self.demuxer_pads.audio_multi)

        self.start_bus_message()

        self.element["decode"].link_pads(self.demuxer_pads.video[0], self.element["tee"], "sink")
        
        do_audio_decklink(self, "decode", "level")      # finish decklink audio element connection (self, name of audio src, name of audio sink)


    def build_pipe(self):
    
     #gst-launch-1.0 uridecodebin uri=file:///z:\\1.mp4 name=de ! queue ! videoscale ! videorate ! videoconvert  ! video/x-raw, format=UYVY, width=1920, height=1080, framerate=60000/1001 ! interlace top-field-first=True field-pattern="1:1" ! decklinkvideosink mode="1080i5994" de. ! queue ! audioresample ! audioconvert ! audio/x-raw, format=S32LE, rate=48000, channels=2, layout=interleaved ! decklinkaudiosink

        updatelog("build pipeline for decklink")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
        
        self.element["source"] = Gst.ElementFactory.make("filesrc")
        self.element["decode"] = Gst.ElementFactory.make("decodebin", "decode")               
        self.element["queue_v"] = Gst.ElementFactory.make("queue", "queue_v")
        
        self.element["videoscale"] =  Gst.ElementFactory.make("videoscale", "videoscale0")
        self.element["videorate"] =  Gst.ElementFactory.make("videorate", "videorate0")
        
        self.element["interlace"] =  Gst.ElementFactory.make("interlace", "interlace0")
        self.element["interlace"].set_property("top-field-first", True)
        self.element["interlace"].set_property("field-pattern", "1:1")
        
        self.element["sink_v"] =  Gst.ElementFactory.make("decklinkvideosink", "sink_v0")
        self.element["sink_v"].set_property("mode", "1080i5994")
        self.element["sink_v"].set_property("device-number", self.decklink)      
        self.element["sink_v"].set_property("video-format", "8bit-yuv")   
        #self.element["sink_v"].set_property("duplex-mode", "half")     
        #self.element["sink_v"].set_property("async", False)         
        
        self.element["tee"] =  Gst.ElementFactory.make("tee")
        self.element["tee"].set_property("allow-not-linked", True)
        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", False)
        self.element["previewbin"] = queue_video_converter_bin("previewbin", "video/x-raw, width=160, height=90")
        self.element["previewbin"].sync_state_with_parent()

        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        self.element["sink_a"] =  Gst.ElementFactory.make("decklinkaudiosink", "sink_a0")
        self.element["sink_a"].set_property("device-number", self.decklink)   
        
        self.element["video_convert"] =  Gst.ElementFactory.make("videoconvert", "video_convert0") 
        self.element["video_convert"].set_property("n-threads", cpu_count)
       
        updatelog(Gst.Element.link(self.element["tee"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")
        
        self.element["cap_v"] = caps_filter("caps_v0", "video/x-raw,format=(string)UYVY,width=1920,height=1080,framerate=60000/1001")

        self.element["level"].set_property("interval", 0.05 * Gst.SECOND)

        for el in self.element:
            updatelog("Add element to pipeline " , el , self.pipeline.add(self.element[el]))

        
        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source"], self.element["decode"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee"], self.element["queue_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v"], self.element["videoscale"]), end="  ")
        updatelog(Gst.Element.link(self.element["videoscale"], self.element["videorate"]), end="  ")
        updatelog(Gst.Element.link(self.element["videorate"], self.element["video_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert"], self.element["cap_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["cap_v"], self.element["interlace"]), end="  ")
        updatelog(Gst.Element.link(self.element["interlace"], self.element["sink_v"]), end="  ")        
        
        updatelog(Gst.Element.link(self.element["tee"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["level"], self.element["sink_a"]))      
        
        updatelog("Finish manual element link --------------------------------------")



        self.element["decode"].connect("pad-added", self.pad_added_handler)
        self.element["decode"].connect("no-more-pads", self.no_more_pad_handler)   
        
        do_preview()        
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS, "decklink_sink")
        
        
    def register_cb_seek(self):
        self.element["tee"].get_static_pad("src_0").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BLOCK, cb_seek_load)



        
class pipeline_decklink_anything_int(myplayer):

    def no_more_pad_handler(self, src):  # mxf demuxer, final stage
        updatelog("There is no more pad on demuxer.... print video and audio pads")
        updatelog("Print all AV stream pads ", self.demuxer_pads.video, self.demuxer_pads.audio)
        for i, c in enumerate(self.demuxer_pads.stream[0]):   
            if c.isdigit():
                updatelog("found first number from demuxer pad name string [location] ", i)
                break
        self.demuxer_pads.audio.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_mono.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_stereo.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        updatelog("Stream list : " , self.demuxer_pads.stream)
        updatelog("Video stream list : " , self.demuxer_pads.video)
        updatelog("Audio stream list : " , self.demuxer_pads.audio)
        updatelog("Audio(mono)   stream list : " , self.demuxer_pads.audio_mono)
        updatelog("Audio(stereo) stream list : " , self.demuxer_pads.audio_stereo)
        updatelog("Audio(multi)  stream list : " , self.demuxer_pads.audio_multi)

        self.start_bus_message()
        
        if len(self.demuxer_pads.video):            # Check if there is video pad
            self.element["decode"].link_pads(self.demuxer_pads.video[0], self.element["pre_queue"], "sink")
        else:
            self.element["sink_v"].set_state(Gst.State.NULL)
            self.pipeline.remove(self.element["sink_v"])        # Remove video sink device

        do_audio_decklink(self, "decode", "level")      # finish decklink audio element connection (self, name of audio src, name of audio sink)
           
        
        try:
            updatelog("  >> Adjust multiqueue size.....")
            mq = self.element["decode"].get_by_name("multiqueue0")
            mq.set_property("max-size-buffers", 200)        # default 5
            mq.set_property("max-size-bytes", 100000000)
            mq.set_property("max-size-time", 5 *Gst.SECOND)
        except:
            updatelog("Error adjusting multiqueue size")


    def build_pipe(self):
    
     #gst-launch-1.0 uridecodebin uri=file:///z:\\1.mp4 name=de ! queue ! videoscale ! videorate ! videoconvert  ! video/x-raw, format=UYVY, width=1920, height=1080, framerate=60000/1001 ! interlace top-field-first=True field-pattern="1:1" ! decklinkvideosink mode="1080i5994" de. ! queue ! audioresample ! audioconvert ! audio/x-raw, format=S32LE, rate=48000, channels=2, layout=interleaved ! decklinkaudiosink

        updatelog("build pipeline for decklink")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
        
        self.element["source"] = Gst.ElementFactory.make("filesrc")
        self.element["decode"] = Gst.ElementFactory.make("decodebin", "decode")               
        self.element["queue_v"] = Gst.ElementFactory.make("queue", "queue_v")
        self.element["queue_v"].set_property("max-size-buffers", 8000)      # default 200
        self.element["queue_v"].set_property("max-size-bytes", 104857600)        # default 10485760
        self.element["queue_v"].set_property("max-size-time", 5 * Gst.SECOND)  # default 1 sec
        
        self.element["pre_queue"] = Gst.ElementFactory.make("queue", "pre_queue") 
        self.element["pre_queue"].set_property("max-size-buffers", 8000)      # default 200
        self.element["pre_queue"].set_property("max-size-bytes", 104857600)        # default 10485760
        self.element["pre_queue"].set_property("max-size-time", 5 * Gst.SECOND)  # default 1 sec
        
        
        self.element["deinterlace"] =  Gst.ElementFactory.make("deinterlace", "deinterlace0")
       
        self.element["videoscale"] =  Gst.ElementFactory.make("videoscale", "videoscale0")
        self.element["videorate"] =  Gst.ElementFactory.make("videorate", "videorate0")
        self.element["interlace"] =  Gst.ElementFactory.make("interlace", "interlace0")
        self.element["interlace"].set_property("top-field-first", True)
        self.element["interlace"].set_property("field-pattern", "1:1")
        
        
        self.element["sink_v"] =  Gst.ElementFactory.make("decklinkvideosink", "sink_v0")
        self.element["sink_v"].set_property("mode", "1080i5994")
        self.element["sink_v"].set_property("device-number", self.decklink)      
        #self.element["sink_v"].set_property("video-format", "8bit-yuv")   
        #self.element["sink_v"].set_property("duplex-mode", "half")     
        #self.element["sink_v"].set_property("async", False)         
        
        self.element["tee"] =  Gst.ElementFactory.make("tee")
        self.element["tee"].set_property("allow-not-linked", True)
        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", False)
        self.element["previewbin"] = queue_video_converter_bin("previewbin", "video/x-raw, width=160, height=90")

        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        self.element["sink_a"] =  Gst.ElementFactory.make("decklinkaudiosink", "sink_a0")
        self.element["sink_a"].set_property("device-number", self.decklink)    
        
        self.element["video_convert"] =  Gst.ElementFactory.make("videoconvert", "video_convert0") 
        self.element["video_convert"].set_property("n-threads", cpu_count)
        
        #self.element["cap_v"] = caps_filter("caps_v0", "video/x-raw,format=(string)UYVY,width=1920,height=1080,framerate=60000/1001")
       
       
        self.element["cap_v"] = caps_filter("caps_v0", "video/x-raw,format=(string)UYVY,width=1920,height=1080,rate=60000/1001")
        updatelog(Gst.Element.link(self.element["tee"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")
        
        self.element["level"].set_property("interval", 0.05 * Gst.SECOND)


        for el in self.element:
            updatelog("Add element to pipeline " , el , self.pipeline.add(self.element[el]))

        
        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source"], self.element["decode"]), end="  ")
        updatelog(Gst.Element.link(self.element["pre_queue"], self.element["tee"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee"], self.element["queue_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v"], self.element["deinterlace"]), end="  ")
        updatelog(Gst.Element.link(self.element["deinterlace"], self.element["videoscale"]), end="  ")        
        updatelog(Gst.Element.link(self.element["videoscale"], self.element["videorate"]), end="  ")
        updatelog(Gst.Element.link(self.element["videorate"], self.element["video_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert"], self.element["cap_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["cap_v"], self.element["interlace"]), end="  ")
        updatelog(Gst.Element.link(self.element["interlace"], self.element["sink_v"]), end="  ")   
        
        updatelog(Gst.Element.link(self.element["tee"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")
        
        
        updatelog(Gst.Element.link(self.element["level"], self.element["sink_a"]))
        updatelog("Finish manual element link --------------------------------------")



        self.element["decode"].connect("pad-added", self.pad_added_handler)
        self.element["decode"].connect("no-more-pads", self.no_more_pad_handler)   
        
        do_preview()        
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS, "decklink_sink")

    def register_cb_seek(self):
        self.element["tee"].get_static_pad("src_0").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BLOCK, cb_seek_load)




class pipeline_decklink_4k_h264_mxf(myplayer):

    def no_more_pad_handler(self, src):  # mxf demuxer, final stage
        updatelog("There is no more pad on demuxer.... print video and audio pads")
        updatelog("Print all AV stream pads ", self.demuxer_pads.video, self.demuxer_pads.audio)
        for i, c in enumerate(self.demuxer_pads.stream[0]):   
            if c.isdigit():
                updatelog("found first number from demuxer pad name string [location] ", i)
                break
        self.demuxer_pads.audio.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_mono.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_stereo.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        updatelog("Stream list : " , self.demuxer_pads.stream)
        updatelog("Video stream list : " , self.demuxer_pads.video)
        updatelog("Audio stream list : " , self.demuxer_pads.audio)
        updatelog("Audio(mono)   stream list : " , self.demuxer_pads.audio_mono)
        updatelog("Audio(stereo) stream list : " , self.demuxer_pads.audio_stereo)
        updatelog("Audio(multi)  stream list : " , self.demuxer_pads.audio_multi)

        self.start_bus_message()
        
        if len(self.demuxer_pads.video) > 0:
            #updatelog("Connect demuxer video pad ", self.element["demuxer"].link_pads(self.demuxer_pads.video[0], self.element["parse_v"], "sink"))
            updatelog("Connect demuxer video pad ", self.element["demuxer"].link_pads(self.demuxer_pads.video[0], self.element["queue_v_pre"], "sink"))

        do_audio_decklink(self, "demuxer", "queue_a")      # finish decklink audio element connection (self, name of audio src, name of audio sink)


    def build_pipe(self):
    
     #gst-launch-1.0 uridecodebin uri=file:///z:\\1.mp4 name=de ! queue ! videoscale ! videorate ! videoconvert  ! video/x-raw, format=UYVY, width=1920, height=1080, framerate=60000/1001 ! interlace top-field-first=True field-pattern="1:1" ! decklinkvideosink mode="1080i5994" de. ! queue ! audioresample ! audioconvert ! audio/x-raw, format=S32LE, rate=48000, channels=2, layout=interleaved ! decklinkaudiosink

        updatelog("build pipeline for decklink")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
        
        self.element["source"] = Gst.ElementFactory.make("filesrc")
        self.element["demuxer"] = Gst.ElementFactory.make("mxfdemux", "demuxer0")   
        
        self.element["queue_v_pre"] = Gst.ElementFactory.make("queue", "queue_v_pre")   
        self.element["parse_v"] = Gst.ElementFactory.make("h264parse", "h264parse0")   
        self.element["queue_v"] = Gst.ElementFactory.make("queue", "queue_v")
        
        self.element["queue_a"] = Gst.ElementFactory.make("queue", "queue_a")
        self.element["queue_a"].set_property("leaky", "downstream")
        self.element["queue_a"].set_property("max-size-buffers", 2000)          # Default 200 buffers
        self.element["queue_a"].set_property("max-size-time", 10 * Gst.SECOND) # Default 1 second
        
        self.element["decoder_v"] = Gst.ElementFactory.make("avdec_h264")  
        self.element["decoder_v"].set_property("max-threads", cpu_count)
        self.element["decoder_v"].set_property("thread-type", 0)            # 0: auto   1: frame   2: slice
        
        
        self.element["sink_v"] =  Gst.ElementFactory.make("decklinkvideosink", "sink_v0")
        updatelog("  [] -- Setup Decklink property " , self.element["sink_v"].set_property("mode", "2160p5994"))
        updatelog("Setup decklink with number " , self.decklink)
        self.element["sink_v"].set_property("device-number", self.decklink)      
        
        self.element["tee"] =  Gst.ElementFactory.make("tee")
        self.element["tee"].set_property("allow-not-linked", True)
        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", False)
        self.element["previewbin"] = queue_video_converter_bin("previewbin", "video/x-raw, width=160, height=90")


        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        self.element["sink_a"] =  Gst.ElementFactory.make("decklinkaudiosink", "sink_a0")
        self.element["sink_a"].set_property("device-number", self.decklink)

        self.element["video_convert"] =  Gst.ElementFactory.make("videoconvert", "video_convert0") 
        self.element["video_convert"].set_property("n-threads", cpu_count)
       
        updatelog(Gst.Element.link(self.element["tee"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")
        
        self.element["cap_v"] = caps_filter("caps_v0", "video/x-raw,format=(string)UYVY,width=3840,height=2160,framerate=60000/1001")

        self.element["level"].set_property("interval", 0.05 * Gst.SECOND)

        for el in self.element:
            updatelog("Add element to pipeline " , el , self.pipeline.add(self.element[el]))

        
        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source"], self.element["demuxer"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["queue_v_pre"], self.element["parse_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["parse_v"], self.element["decoder_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["decoder_v"], self.element["tee"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee"], self.element["queue_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v"], self.element["video_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert"], self.element["cap_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["cap_v"], self.element["sink_v"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["tee"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["queue_a"], self.element["level"]), end="  ")
        updatelog(Gst.Element.link(self.element["level"], self.element["sink_a"]))
        updatelog("Finish manual element link --------------------------------------")



        self.element["demuxer"].connect("pad-added", self.pad_added_handler)
        self.element["demuxer"].connect("no-more-pads", self.no_more_pad_handler)   
        
        do_preview()        
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS, "decklink_sink")

    def register_cb_seek(self):
        self.element["tee"].get_static_pad("src_0").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BLOCK, cb_seek_load)




class pipeline_desktop_capture(myplayer):



    def no_more_pad_handler(self, src):  # mxf demuxer, final stage
        updatelog("There is no more pad on demuxer.... print video and audio pads")
        updatelog("Print all AV stream pads ", self.demuxer_pads.video, self.demuxer_pads.audio)
        for i, c in enumerate(self.demuxer_pads.stream[0]):   
            if c.isdigit():
                updatelog("found first number from demuxer pad name string [location] ", i)
                break
        self.demuxer_pads.audio.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_mono.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_stereo.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        updatelog("Stream list : " , self.demuxer_pads.stream)
        updatelog("Video stream list : " , self.demuxer_pads.video)
        updatelog("Audio stream list : " , self.demuxer_pads.audio)
        updatelog("Audio(mono)   stream list : " , self.demuxer_pads.audio_mono)
        updatelog("Audio(stereo) stream list : " , self.demuxer_pads.audio_stereo)
        updatelog("Audio(multi)  stream list : " , self.demuxer_pads.audio_multi)

        self.start_bus_message()
        
        if len(self.demuxer_pads.video) > 0:
            updatelog("Connect demuxer video pad ", self.element["demuxer"].link_pads(self.demuxer_pads.video[0], self.element["decoder_v"], "sink"))


    def build_pipe(self):
    
        #gst-launch-1.0 dx9screencapsrc ! queue ! videoconvert ! videorate ! videoscale ! video/x-raw, width=1920, height=1080, framerate=60000/1001 ! queue !  interlace top-field-first=true  field-pattern="1:1" ! decklinkvideosink device-number=1 mode=1080i5994

        updatelog("build pipeline for desktop_capture")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
        
        self.element["source"] = Gst.ElementFactory.make("dx9screencapsrc")
      
        self.element["queue_v"] = Gst.ElementFactory.make("queue", "queue_v")
        self.element["queue_v2"] = Gst.ElementFactory.make("queue", "queue_v2")


        self.element["video_convert"] =  Gst.ElementFactory.make("videoconvert", "video_convert0") 
        self.element["video_convert"].set_property("n-threads", cpu_count)

        self.element["video_rate"] =  Gst.ElementFactory.make("videorate", "video_rate0") 
        self.element["video_scale"] =  Gst.ElementFactory.make("videoscale", "video_scale0") 
        self.element["video_scale"].set_property("n-threads", cpu_count)

        self.element["video_interlace"] =  Gst.ElementFactory.make("interlace", "interlace0") 
        self.element["video_interlace"].set_property("field-pattern", "1:1")
        self.element["video_interlace"].set_property("top-field-first", True)
        
        setup_queue_size(self.element["queue_v"], 2000, 100, 5)       # Create large queue size, added 2022/1/25
        setup_queue_size(self.element["queue_v2"], 2000, 100, 5) 

         
        self.element["sink_v"] =  Gst.ElementFactory.make("decklinkvideosink", "sink_v0")
        updatelog("  [] -- Setup Decklink property " , self.element["sink_v"].set_property("mode", "1080i5994"))
        updatelog("Setup decklink with number " , self.decklink)
        self.element["sink_v"].set_property("device-number", self.decklink)      
        self.element["sink_v"].set_property("video-format", "8bit-yuv")   
   
        self.element["cap_v"] = caps_filter("caps_v0", "video/x-raw,format=(string)UYVY,width=1920,height=1080,framerate=60000/1001")

        for el in self.element:
            updatelog("Add element to pipeline " , el , self.pipeline.add(self.element[el]))

        updatelog("Start manual element link --------------------------------------")
        
        updatelog(Gst.Element.link(self.element["source"], self.element["queue_v"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["queue_v"], self.element["video_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert"], self.element["video_rate"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_rate"], self.element["video_scale"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_scale"], self.element["cap_v"]), end="  ")                
        updatelog(Gst.Element.link(self.element["cap_v"], self.element["queue_v2"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v2"], self.element["video_interlace"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_interlace"], self.element["sink_v"]), end="  ")

        updatelog("Finish manual element link --------------------------------------")

       
        #do_preview()        
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS, "decklink_sink")

    def register_cb_seek(self):
        #self.element["queue_v_output"].get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BLOCK , cb_seek_load)
        self.element["queue_v"].get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BLOCK, cb_seek_load)





class pipeline_decklink_mxf(myplayer):

    def no_more_pad_handler(self, src):  # mxf demuxer, final stage
        updatelog("There is no more pad on demuxer.... print video and audio pads")
        updatelog("Print all AV stream pads ", self.demuxer_pads.video, self.demuxer_pads.audio)
        for i, c in enumerate(self.demuxer_pads.stream[0]):   
            if c.isdigit():
                updatelog("found first number from demuxer pad name string [location] ", i)
                break
        self.demuxer_pads.audio.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_mono.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_stereo.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        updatelog("Stream list : " , self.demuxer_pads.stream)
        updatelog("Video stream list : " , self.demuxer_pads.video)
        updatelog("Audio stream list : " , self.demuxer_pads.audio)
        updatelog("Audio(mono)   stream list : " , self.demuxer_pads.audio_mono)
        updatelog("Audio(stereo) stream list : " , self.demuxer_pads.audio_stereo)
        updatelog("Audio(multi)  stream list : " , self.demuxer_pads.audio_multi)

        self.start_bus_message()
        
        if len(self.demuxer_pads.video) > 0:
            updatelog("Connect demuxer video pad ", self.element["demuxer"].link_pads(self.demuxer_pads.video[0], self.element["decoder_v"], "sink"))

        do_audio_decklink(self, "demuxer", "queue_a")      # finish decklink audio element connection (self, name of audio src, name of audio sink)


    def build_pipe(self):
    
     #gst-launch-1.0 uridecodebin uri=file:///z:\\1.mp4 name=de ! queue ! videoscale ! videorate ! videoconvert  ! video/x-raw, format=UYVY, width=1920, height=1080, framerate=60000/1001 ! interlace top-field-first=True field-pattern="1:1" ! decklinkvideosink mode="1080i5994" de. ! queue ! audioresample ! audioconvert ! audio/x-raw, format=S32LE, rate=48000, channels=2, layout=interleaved ! decklinkaudiosink

        updatelog("build pipeline for decklink")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
        
        self.element["source"] = Gst.ElementFactory.make("filesrc")
        self.element["demuxer"] = Gst.ElementFactory.make("mxfdemux", "demuxer0")   
        
      
        self.element["queue_v"] = Gst.ElementFactory.make("queue", "queue_v")
        self.element["queue_v_output"] = Gst.ElementFactory.make("queue", "queue_v_output")
        #self.element["mpeg_v_parser"] = Gst.ElementFactory.make("mpegvideoparse", "mpeg_v_parser0")
        self.element["queue_a"] = Gst.ElementFactory.make("queue", "queue_a")
        self.element["queue_a"].set_property("leaky", "downstream")
        
        setup_queue_size(self.element["queue_v"], 2000, 100, 5)       # Create large queue size, added 2022/1/25
        setup_queue_size(self.element["queue_v_output"], 2000, 100, 5) 
        setup_queue_size(self.element["queue_a"], 2000, 100, 5)   

        
        self.element["decoder_v"] = Gst.ElementFactory.make("avdec_mpegvideo")  
        
        self.element["sink_v"] =  Gst.ElementFactory.make("decklinkvideosink", "sink_v0")
        updatelog("  [] -- Setup Decklink property " , self.element["sink_v"].set_property("mode", "1080i5994"))
        updatelog("Setup decklink with number " , self.decklink)
        self.element["sink_v"].set_property("device-number", self.decklink)      
        self.element["sink_v"].set_property("video-format", "8bit-yuv")   
        #self.element["sink_v"].set_property("duplex-mode", "half")     
        #self.element["sink_v"].set_property("async", False)         
        
        self.element["tee"] =  Gst.ElementFactory.make("tee")
        self.element["tee"].set_property("allow-not-linked", True)
        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", False)
        self.element["previewbin"] = queue_video_converter_bin("previewbin", "video/x-raw, width=160, height=90")


        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        self.element["sink_a"] =  Gst.ElementFactory.make("decklinkaudiosink", "sink_a0")
        self.element["sink_a"].set_property("device-number", self.decklink)

        self.element["video_convert"] =  Gst.ElementFactory.make("videoconvert", "video_convert0") 
        self.element["video_convert"].set_property("n-threads", cpu_count)
       
        updatelog(Gst.Element.link(self.element["tee"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")
        
        self.element["cap_v"] = caps_filter("caps_v0", "video/x-raw,format=(string)UYVY,width=1920,height=1080,framerate=30000/1001")

        self.element["level"].set_property("interval", 0.05 * Gst.SECOND)

        for el in self.element:
            updatelog("Add element to pipeline " , el , self.pipeline.add(self.element[el]))

        
        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source"], self.element["demuxer"]), end="  ")
        
        #updatelog(Gst.Element.link(self.element["mpeg_v_parser"], self.element["decoder_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["decoder_v"], self.element["tee"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee"], self.element["queue_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v"], self.element["video_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert"], self.element["cap_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["cap_v"], self.element["queue_v_output"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v_output"], self.element["sink_v"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["tee"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["queue_a"], self.element["level"]), end="  ")
        updatelog(Gst.Element.link(self.element["level"], self.element["sink_a"]))
        updatelog("Finish manual element link --------------------------------------")



        self.element["demuxer"].connect("pad-added", self.pad_added_handler)
        self.element["demuxer"].connect("no-more-pads", self.no_more_pad_handler)   
        
        do_preview()        
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS, "decklink_sink")

    def register_cb_seek(self):
        #self.element["queue_v_output"].get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BLOCK , cb_seek_load)
        self.element["tee"].get_static_pad("src_0").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BLOCK, cb_seek_load)


class pipeline_decklink_hd_prores_mov(myplayer):

    def no_more_pad_handler(self, src):  # mxf demuxer, final stage
        updatelog("There is no more pad on demuxer.... print video and audio pads")
        updatelog("Print all AV stream pads ", self.demuxer_pads.video, self.demuxer_pads.audio)
        for i, c in enumerate(self.demuxer_pads.stream[0]):   
            if c.isdigit():
                updatelog("found first number from demuxer pad name string [location] ", i)
                break
        self.demuxer_pads.audio.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_mono.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_stereo.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        updatelog("Stream list : " , self.demuxer_pads.stream)
        updatelog("Video stream list : " , self.demuxer_pads.video)
        updatelog("Audio stream list : " , self.demuxer_pads.audio)
        updatelog("Audio(mono)   stream list : " , self.demuxer_pads.audio_mono)
        updatelog("Audio(stereo) stream list : " , self.demuxer_pads.audio_stereo)
        updatelog("Audio(multi)  stream list : " , self.demuxer_pads.audio_multi)

        self.start_bus_message()
        
        if len(self.demuxer_pads.video) > 0:
            updatelog("Connect demuxer video pad ", self.element["demuxer"].link_pads(self.demuxer_pads.video[0], self.element["queue_v_pre"], "sink"))

        do_audio_decklink(self, "demuxer", "queue_a")      # finish decklink audio element connection (self, name of audio src, name of audio sink)


    def build_pipe(self):
    
     #gst-launch-1.0 uridecodebin uri=file:///z:\\1.mp4 name=de ! queue ! videoscale ! videorate ! videoconvert  ! video/x-raw, format=UYVY, width=1920, height=1080, framerate=60000/1001 ! interlace top-field-first=True field-pattern="1:1" ! decklinkvideosink mode="1080i5994" de. ! queue ! audioresample ! audioconvert ! audio/x-raw, format=S32LE, rate=48000, channels=2, layout=interleaved ! decklinkaudiosink

        updatelog("build pipeline for decklink")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
        
        self.element["source"] = Gst.ElementFactory.make("filesrc")
        self.element["demuxer"] = Gst.ElementFactory.make("qtdemux", "demuxer0")   
        
      
        self.element["queue_v"] = Gst.ElementFactory.make("queue", "queue_v")
        self.element["queue_v_pre"] = Gst.ElementFactory.make("queue", "queue_v_pre")
        
        self.element["queue_a"] = Gst.ElementFactory.make("queue", "queue_a")
        self.element["queue_a"].set_property("leaky", "downstream")
        self.element["queue_a"].set_property("max-size-buffers", 2000)          # Default 200 buffers
        self.element["queue_a"].set_property("max-size-time", 10 * Gst.SECOND) # Default 1 second
        self.element["decoder_v"] = Gst.ElementFactory.make("avdec_prores")  
        self.element["decoder_v"].set_property("max-threads", cpu_count)
        self.element["decoder_v"].set_property("thread-type", 2)            # 0: auto   1: frame   2: slice
        
        
        self.element["sink_v"] =  Gst.ElementFactory.make("decklinkvideosink", "sink_v0")
        updatelog("  [] -- Setup Decklink property " , self.element["sink_v"].set_property("mode", "1080i5994"))
        updatelog("Setup decklink with number " , self.decklink)
        self.element["sink_v"].set_property("device-number", self.decklink)      
        self.element["sink_v"].set_property("video-format", "8bit-yuv")   
        #self.element["sink_v"].set_property("duplex-mode", "half")     
        #self.element["sink_v"].set_property("async", False)         
        
        self.element["tee"] =  Gst.ElementFactory.make("tee")
        self.element["tee"].set_property("allow-not-linked", True)
        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", False)
        self.element["previewbin"] = queue_video_converter_bin("previewbin", "video/x-raw, width=160, height=90")


        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        self.element["sink_a"] =  Gst.ElementFactory.make("decklinkaudiosink", "sink_a0")
        self.element["sink_a"].set_property("device-number", self.decklink)

        self.element["video_convert"] =  Gst.ElementFactory.make("videoconvert", "video_convert0") 
        self.element["video_convert"].set_property("n-threads", cpu_count)
       
        updatelog(Gst.Element.link(self.element["tee"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")
        
        self.element["cap_v"] = caps_filter("caps_v0", "video/x-raw,format=(string)UYVY,width=1920,height=1080,framerate=30000/1001")

        self.element["level"].set_property("interval", 0.05 * Gst.SECOND)

        for el in self.element:
            updatelog("Add element to pipeline " , el , self.pipeline.add(self.element[el]))

        
        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source"], self.element["demuxer"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["queue_v_pre"], self.element["decoder_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["decoder_v"], self.element["tee"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee"], self.element["queue_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v"], self.element["video_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert"], self.element["cap_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["cap_v"], self.element["sink_v"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["tee"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["queue_a"], self.element["level"]), end="  ")
        updatelog(Gst.Element.link(self.element["level"], self.element["sink_a"]))
        updatelog("Finish manual element link --------------------------------------")



        self.element["demuxer"].connect("pad-added", self.pad_added_handler)
        self.element["demuxer"].connect("no-more-pads", self.no_more_pad_handler)   
        
        do_preview()        
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.CAPS_DETAILS, "decklink_sink")

    def register_cb_seek(self):
        self.element["tee"].get_static_pad("src_0").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BLOCK, cb_seek_load)


class pipeline_decklink_4k_prores(myplayer):

    def no_more_pad_handler(self, src):  # mxf demuxer, final stage

        updatelog("There is no more pad on demuxer.... print video and audio pads")
        updatelog("Print all AV stream pads ", self.demuxer_pads.video, self.demuxer_pads.audio)
        for i, c in enumerate(self.demuxer_pads.stream[0]):   
            if c.isdigit():
                updatelog("found first number from demuxer pad name string [location] ", i)
                break
        self.demuxer_pads.audio.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_mono.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        self.demuxer_pads.audio_stereo.sort(key=lambda x: int(x[i:]))   # sort like human (track_xx xx part is key)
        updatelog("Stream list : " , self.demuxer_pads.stream)
        updatelog("Video stream list : " , self.demuxer_pads.video)
        updatelog("Audio stream list : " , self.demuxer_pads.audio)
        updatelog("Audio(mono)   stream list : " , self.demuxer_pads.audio_mono)
        updatelog("Audio(stereo) stream list : " , self.demuxer_pads.audio_stereo)
        updatelog("Audio(multi)  stream list : " , self.demuxer_pads.audio_multi)

        self.start_bus_message()

       
        updatelog(" >>>  Multi channel audio number is " + str(self.demuxer_pads.audio_many_channel))

        updatelog(" == Connect demuxer V/A track to V/A queue ==")
        if len(self.demuxer_pads.video):
            updatelog(self.element["demux"].link_pads(self.demuxer_pads.video[0], self.element["queue_v"], "sink"))
        else:
            self.pipeline.remove(self.element["sink_v"])



        self.start_bus_message()
        
        do_audio_decklink(self, "demux", "level")      # finish decklink audio element connection (self, name of audio src, name of audio sink)
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline,  Gst.DebugGraphDetails.STATES | Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "decklink_no_more_pads")



    def build_pipe(self):
# UHD 4k command_line ---------------------------------------------------
#C:\ahk\gstreamer>gst-launch-1.0  filesrc location=D:\\baseball_UHD_16ch.mov ! qtdemux ! queue "max-size-buffers=1000" ! avdec_prores "max-threads=32" "thread-type=2" ! videoconvert  "n-threads=32"  ! video/x-raw,format=UYVY,framerate=60000/1001 ! queue "max-size-bytes=50000000" ! decklinkvideosink "device-number=1" "mode=2160p5994"
        updatelog(" -- build pipeline for decklink")
        
        cpu_count = os.cpu_count()
        updatelog("Number of CPU is {}".format(cpu_count))
        
        self.element["source"] = Gst.ElementFactory.make("filesrc")
        self.element["queue_v"] = Gst.ElementFactory.make("queue", "queue_v")
        self.element["queue_v2"] = Gst.ElementFactory.make("queue", "queue_v2")
        #self.element["queue_a"] = Gst.ElementFactory.make("queue", "queue_a")        

        self.element["queue_v_last"] = Gst.ElementFactory.make("queue", "queue_v_last")
        #self.element["queue_a_last"] = Gst.ElementFactory.make("queue", "queue_a_last")    

        self.element["queue_v"].set_property("max-size-buffers", 4000)
        self.element["queue_v"].set_property("max-size-bytes", 900000000)
        self.element["queue_v"].set_property("max-size-time", 5 * Gst.SECOND)

        self.element["queue_v2"].set_property("max-size-buffers", 4000)
        self.element["queue_v2"].set_property("max-size-bytes", 900000000)
        self.element["queue_v2"].set_property("max-size-time", 5 * Gst.SECOND)
        
        self.element["queue_v_last"].set_property("max-size-buffers", 4000)
        self.element["queue_v_last"].set_property("max-size-bytes", 900000000)
        self.element["queue_v_last"].set_property("max-size-time", 5 * Gst.SECOND)


        self.element["sink_video"] =  Gst.ElementFactory.make("decklinkvideosink", "sink_v0")
        self.element["sink_video"].set_property("mode", "2160p5994")
        self.element["sink_video"].set_property("device-number", self.decklink)        
        
        self.element["tee"] =  Gst.ElementFactory.make("tee")
        self.element["tee"].set_property("allow-not-linked", True)
        self.element["sink_gtk"] = Gst.ElementFactory.make("gtksink") 
        self.element["sink_gtk"].set_property("async", False)
        self.element["previewbin"] = queue_video_converter_bin("previewbin", "video/x-raw, width=160, height=90")

        self.element["level"] =  Gst.ElementFactory.make("level", "level0")
        self.element["level"].set_property("interval", 0.05 * Gst.SECOND)   

        
        self.element["sink_audio"] =  Gst.ElementFactory.make("decklinkaudiosink", "sink_a0")
        self.element["sink_audio"].set_property("device-number", self.decklink)       
        
        #self.element["resample"] =  Gst.ElementFactory.make("audioresample", "audio_resample_end")
        self.element["video_convert"] =  Gst.ElementFactory.make("videoconvert", "video_convert0") 
        self.element["video_convert"].set_property("n-threads", cpu_count)

        self.element["demux"] =  Gst.ElementFactory.make("qtdemux")
        self.element["demux"].connect("pad-added", self.pad_added_handler)
        self.element["demux"].connect("no-more-pads", self.no_more_pad_handler)

        self.element["decoder_v"] = Gst.ElementFactory.make("avdec_prores")
        self.element["decoder_v"].set_property("max-threads", cpu_count)
        self.element["decoder_v"].set_property("thread-type", 2)            # 0: auto   1: frame   2: slice
               

        self.element["cap_v"] = caps_filter("caps_v0", "video/x-raw,format=(string)UYVY,width=3840,height=2160,framerate=60000/1001")

        #self.element["cap_a"] = caps_filter("caps_a0", "audio/x-raw,format=(string)S32LE,rate=48000,channels=(int)16,layout=(string)interleaved")

        for el in self.element:
            updatelog("Add element to pipeline " , el , self.pipeline.add(self.element[el]))

        
        updatelog("Start manual element link --------------------------------------")
        updatelog(Gst.Element.link(self.element["source"], self.element["demux"]), end="  ")
        
        updatelog(Gst.Element.link(self.element["queue_v"], self.element["decoder_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["decoder_v"], self.element["tee"]), end="  ")
        updatelog(Gst.Element.link(self.element["tee"], self.element["queue_v2"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v2"], self.element["video_convert"]), end="  ")
        updatelog(Gst.Element.link(self.element["video_convert"], self.element["cap_v"]), end="  ")
        updatelog(Gst.Element.link(self.element["cap_v"], self.element["queue_v_last"]), end="  ")
        updatelog(Gst.Element.link(self.element["queue_v_last"], self.element["sink_video"]), end="  ")    

        updatelog(Gst.Element.link(self.element["tee"], self.element["previewbin"]), end="  ")
        updatelog(Gst.Element.link(self.element["previewbin"], self.element["sink_gtk"]), end="  ")

        Gst.Element.link(self.element["level"], self.element["sink_audio"])
        updatelog("Finish manual element link --------------------------------------")


        do_preview()      
        
        
        # Print debug dot file
        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.FULL_PARAMS | Gst.DebugGraphDetails.STATES | Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "decklink_sink")

    def register_cb_seek(self):
        self.element["tee"].get_static_pad("src_0").add_probe(Gst.PadProbeType.BUFFER | Gst.PadProbeType.BLOCK, cb_seek_load)
        


def set_scale():
    global player, gui, args_list, wave_gui
    success, duration = player.pipeline.query_duration(Gst.Format.TIME)
    
    if not success:
        print("\r\n  --)))  set_scale >> Couldn't fetch song duration @@@@@@@@@@@@@  ")
        duration = 86400 * Gst.SECOND
        return
    else:
        if duration < 0:
            duration = 86400 * Gst.SECOND
        gui.slider.set_range(0, duration / Gst.SECOND)
        
    #fetching the position, in nanosecs
    success, position = player.pipeline.query_position(Gst.Format.TIME)
    if not success:
        print("\r\n  -->>> set_scale >> Couldn't fetch current song position to update slider @@@@@@@@@@@@@")
        return

    # block seek handler so we don't seek when we set_value()
    gui.slider.handler_block(gui.slider_handler_id)
    gui.slider.set_value(float(position) / Gst.SECOND)
    gui.slider.handler_unblock(gui.slider_handler_id)    

    if (args_list.args.preset == "audio_only"):
        wave_gui.set_progress(float(position) / duration)
    

def set_scale_old():
    global player, gui
    success, duration = player.pipeline.query_duration(Gst.Format.TIME)
    if not success:
        raise GenericException("Couldn't fetch song duration @@@@@@@@@@@@@")
    else:
        gui.slider.set_range(0, duration / Gst.SECOND)
    #fetching the position, in nanosecs
    success, position = player.pipeline.query_position(Gst.Format.TIME)
    if not success:
        raise GenericException("Couldn't fetch current song position to update slider @@@@@@@@@@@@@")

    # block seek handler so we don't seek when we set_value()
    gui.slider.handler_block(gui.slider_handler_id)
    gui.slider.set_value(float(position) / Gst.SECOND)
    gui.slider.handler_unblock(gui.slider_handler_id)    

    
def do_goto_top():      # changed TRICKMODE_NO_AUDIO  -> TRICKMODE     (2022/2/25)
    global player
    updatelog("Goto Top, or Mark in")

    result = player.pipeline.seek_simple(Gst.Format.TIME, (Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE  | Gst.SeekFlags.TRICKMODE) , float(player.mark_in) * Gst.SECOND) 
    updatelog(result)
    player.pipeline.set_state(Gst.State.PAUSED)


def do_load():
    global gui, player, osc
    player.load(gui.entry_get_text())
    updatelog("player pipeline state while load is {0}".format(player.pipeline.get_state(Gst.CLOCK_TIME_NONE)))
    if player.mark_in or player.length:
        player.register_cb_seek()
        updatelog("Mark_in or duration option detected.. value is {0}/{1}".format(player.mark_in, player.length))
    gui.set_title("G Engine Server   //  " + player.clip)

def cb_seek_load(pad, pad_info):
    updatelog("\r\n   ----------  probe callback, seek while load ---------------")
    threading.Thread(target = do_seek_load).start()
    return Gst.PadProbeReturn.REMOVE            # Remove this callback


def do_seek_load():
    global player
    updatelog("Executing accurate seek command")
    if player.length:
        time_stop = (player.mark_in + player.length) * Gst.SECOND
    else:
        time_stop = 86400 *  Gst.SECOND
    updatelog("mark_in is {}".format(player.mark_in))
    updatelog("length found... stop time is {}".format(time_stop))
    result = player.pipeline.seek(1.00 , Gst.Format.TIME, (Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE) , Gst.SeekType.SET, float(player.mark_in) * Gst.SECOND, Gst.SeekType.SET, time_stop )

    #player.pipeline.seek_simple(Gst.Format.TIME,  Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, float(player.mark_in) * Gt.SECOND)    
    updatelog("\r\n --> seek result is {}".format(result))
    set_scale()
    updatelog(" ------------ Finish seek load ------------------  ")
    
def do_play():
    global player
    player.play()
    #updatelog("Pipeline clock is " , player.pipeline.get_pipeline_clock())
    Gst.debug_bin_to_dot_file(player.pipeline,  Gst.DebugGraphDetails.STATES | Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "Dump_play_" + time.strftime("%Y%m%d-%H%M%S"))


def do_pause():
    global player
    player.pause()   
    Gst.debug_bin_to_dot_file(player.pipeline,  Gst.DebugGraphDetails.STATES | Gst.DebugGraphDetails.CAPS_DETAILS | Gst.DebugGraphDetails.ALL, "Dump_pause_" + time.strftime("%Y%m%d-%H%M%S"))    


def do_slider_seek():
    global player, gui
    seek_time_secs = gui.slider.get_value()
    #player.pipeline.seek_simple(Gst.Format.TIME,  Gst.SeekFlags.TRICKMODE_NO_AUDIO | Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, seek_time_secs * Gst.SECOND) 
    player.pipeline.seek_simple(Gst.Format.TIME,  Gst.SeekFlags.TRICKMODE | Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, seek_time_secs * Gst.SECOND) 
    

    
def do_tcp_seek(seek_time_secs):
    global player
    updatelog("seek position is ----------------   " , str(seek_time_secs))
    #player.pipeline.seek_simple(Gst.Format.TIME,  Gst.SeekFlags.TRICKMODE_NO_AUDIO |  Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, seek_time_secs * Gst.SECOND) 
    player.pipeline.seek_simple(Gst.Format.TIME,  Gst.SeekFlags.TRICKMODE |  Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, seek_time_secs * Gst.SECOND)             # changed 2022/3/2  (do not use TRICKMODE_NO_AUDIO flag !!!)
    
    
    
def do_level_init():
    osc.init_volume()
    
class debugwindow(Gtk.Window):
    text_list = []           # create blank list
    
    def __init__(self):
        updatelog("Create debug window class  --------------- ")    
        super().__init__(title="Debug window")
        self.set_size_request(500, 400)  
        self.connect("destroy", self.winclose)
        box = Gtk.Box(spacing=6)
        box.set_size_request(500,400)
        self.add(box)
        self.label1 = Gtk.Label.new("Debug screen1")
        self.label1.set_size_request(450,400)
        self.label1.set_xalign(0.01)
        self.label1.set_yalign(0.01)
        self.label2 = Gtk.Label.new("Debug screen2")
        self.label2.set_size_request(450,400)
        self.label2.set_xalign(0.01)
        self.label2.set_yalign(0.01)
        box.pack_start(self.label1, True, True, 0)
        box.pack_start(self.label2, True, True, 0)
        self.text_list = ["Information"]
        
    
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


class waveform_draw(Gtk.Window):
    thread = ""
    
    def __init__(self):
        updatelog("Create waveform_draw class  --------------- ")
        super().__init__(title="Waveform viewer by sendust //")
        self.set_size_request(1024, 400)  
        self.connect("destroy", self.winclose)
        self.props.resizable = False

    def show_gui(self):
        overlay = Gtk.Overlay()
        self.add(overlay)
        
        #image = Gtk.Image().new_from_file("waveform.png")
        self.image = Gtk.Image.new()
        self.image.set_size_request(1024, 400)
        
        
        self.progress = Gtk.ProgressBar()
        self.progress.set_valign(Gtk.Align.CENTER)
        self.progress.set_halign(Gtk.Align.CENTER)
        self.progress.set_size_request(1024, -1)
        self.progress.set_fraction(0.5)
        
        overlay.add_overlay(self.progress)
        overlay.add(self.image)
        self.show_all()

    def set_new_title(self, text):
        print("Setup wave for GUI title with ", text)
        self.set_title(text)   

    def load_png(self):
        self.image.props.file = "waveform.png"
        
        
    def winclose(self, something):
        print("Close waveform GUI")
        self.close()

    def set_progress(self, value):
        self.progress.set_fraction(value)

    def move_primary(self, x, y):
        rect2 = self.get_screen().get_display().get_monitor(0).get_geometry()   # Get primary monitor geometry (x,y,width,height)
        self.move(rect2.x + x, rect2.y + y)
        
        

class mywindow(Gtk.Window):
    def __init__(self):
        updatelog("Create GUI window class  --------------- ")
        super().__init__(title="G Engine Server")
        self.set_size_request(870, 110)  
        #self.set_border_width(10)
        self.connect("destroy", os_exit)
        
        self.layout = Gtk.Layout()
        self.layout.set_size(870, 110)
        
        button1 = Gtk.Button(label="|<")
        button1.connect("clicked", self.on_button1)
        self.layout.put(button1, 10, 15)

        button2 = Gtk.Button(label="▷")
        button2.connect("clicked", self.on_button2)
        self.layout.put(button2, 60, 15)

        button3 = Gtk.Button(label="||")
        button3.connect("clicked", self.on_button3)
        self.layout.put(button3, 110, 15)

        self.slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 0.5)
        self.slider.set_size_request(500, 10)
        self.layout.put(self.slider, 180, 10)
        self.slider_handler_id = self.slider.connect("value-changed", self.on_slider_seek)

        self.entry = Gtk.Entry.new()
        self.entry.set_size_request(660, 10)
        self.entry.set_text("Drag drop Media file here")
        self.layout.put(self.entry, 10, 60)

        
        self.connect('drag_data_received', on_drag_data_received)
        self.drag_dest_set( Gtk.DestDefaults.MOTION|
                  Gtk.DestDefaults.HIGHLIGHT | Gtk.DestDefaults.DROP,
                  [Gtk.TargetEntry.new("text/uri-list", 0, 80)], Gdk.DragAction.COPY)


        self.add(self.layout)
        
        self.set_resizable(False)       # disable window resize


    def on_button1(self, widget):       # Goto Top button
        updatelog("button1 Pressed")
        self.entry_get_text()
        do_goto_top()
            
    def on_button2(self, widget):       # play button
        updatelog("button2 Pressed")
        do_play()
        
    def on_button3(self, widget):       # pause button
        updatelog("button3 Pressed")
        do_pause()

    def on_slider_seek(self, widget):
        updatelog("slider move  " + str(self.slider.get_value()))
        do_slider_seek()
    
    def show_gui(self):
        self.show_all()
    
    def run(self):
        updatelog("Start Gtk main loop....................")
        Gtk.main()

    def entry_get_text(self):
        text = self.entry.get_text()
        updatelog("Entry text is " + text)
        return text
        
    def entry_set_text(self, text):
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
        
        self.volume = [-700,-700,-700,-700,-700,-700,-700,-700] # 8 channel audio level meter array
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
        self.volume = [-700,-700,-700,-700,-700,-700,-700,-700] # 8 channel audio level meter array
    
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
                        + "\nout_time_ms**" + str(int(self.time_run * 1000000))   # New  2022/4/20, ffmpeg compatible      
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
        #threading.Timer(0.1,self.send_udp, [host, port]).start()

def do_osc2():
    pass


def do_osc():
    global player, osc, debug_gui
    
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
        print("do_osc>> Couldn't fetch media duration   -- tick.." + str(time.perf_counter()), end="\r")
        return True
    else:
        if (duration < 0):
            duration = 86400 * Gst.SECOND               # 24 Hour
        osc.time_dur = duration / Gst.SECOND
        #if (player.length > osc.time_dur) | (int(player.length) == 0):  # acquire new length from pipeline query
        if int(player.length) == 0:  # acquire new length from pipeline query
            # updatelog("info  >>>>>>  Get new duration from pipeline query duration")
            debug_gui.append_text("info  >>>>>>  Get new duration from pipeline query duration" )
            player.length = osc.time_dur - player.mark_in
            

    success, position = player.pipeline.query_position(Gst.Format.TIME)
    if not success:
        updatelog("do_osc>> Couldn't fetch current song position to update slider")
        return True
    else:
        osc.time_run = position / Gst.SECOND
    
    volume_int = [ int(x) for x in osc.volume]    # convert short number to print console
    print("{3} / osc data is time_dur {0} time_run {1} audio level {2}     ".format(osc.time_dur, osc.time_run, volume_int, player.pipeline_state), end="\r")
    
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
    updatelog("Window destroyed..... Exit to OS")
    Gtk.main_quit()

    

def do_args(args):
    global player, ffmpeg, wave_gui

    if (args.preset == "decklink_4k_prores"):
        player =  pipeline_decklink_4k_prores()
    elif (args.preset == "decklink_hd_mxf"):
        player = pipeline_decklink_mxf()
    elif (args.preset == "decklink_hd_anything"):
        player = pipeline_decklink_anything()
    elif (args.preset == "decklink_hd_anything_int"):
        player = pipeline_decklink_anything_int()
    elif (args.preset == "decklink_4k_h264_mxf"):
        player = pipeline_decklink_4k_h264_mxf()
    elif (args.preset == "decklink_hd_prores_mov"):
        player = pipeline_decklink_hd_prores_mov()        
    elif (args.preset == "desktop_capture"):
        player = pipeline_desktop_capture()
    elif (args.preset == "audio_only"):
        player = pipeline_audio_only()


    else:
        player = pipeline_vga_anything()
    

    player.decklink = args.decklink    
    player.clip = args.clip
    player.auto_start = args.auto_start
    player.guid = args.guid
    player.build_pipe()
    
    if not os.path.isfile(player.clip):
        updatelog("File not exist... Terminate program")
        time.sleep(0.5)
        sys.exit(1)
    
    gui.entry_set_text(player.clip)         
    ctypes.windll.kernel32.SetConsoleTitleW("G-Engine Server by sendust   //  " + args.clip)          ## Change console title

    
    if args.mark_in:
        player.mark_in = args.mark_in       # setup player mark_in
    else:
        player.mark_in = 0
    if args.length:
        player.length = args.length     # setup play length
    else:
        player.length = 0
    
    if (args.preset == "audio_only"):   # make waveform
        name = "\"" + args.clip + "\""
        name = name.replace("\"\"", "\"")    
        ffmpeg = subprocess_run()
        ffmpeg.run("ffmpeg -i " + name + " -filter_complex showwavespic=size=1024x400:split_channels=1  -y waveform.png")
        wave_gui = waveform_draw()
        wave_gui.show_gui()
        wave_gui.move_primary(5, 155)
        
    updatelog(vars(player))



def do_tcp_command(cmd):
    updatelog("TCP command {0} accepted".format(cmd))
    if cmd == "PLAY":
        do_play()
    if cmd == "PAUSE":
        do_pause()
    if cmd[0:4] == "SEEK":
        position = float(cmd[5:])
        do_tcp_seek(position)       # Parse seek command
    
        

    
class cli_parser():
    def __init__(self):
        updatelog("Create argument parser")
        self.parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent('''\
        G_Engine player powered by sendust
          
          Preset list --------
             - vga_anything
             - decklink_hd_anything
             - decklink_hd_anything_int
             - decklink_hd_mxf
             - decklink_hd_prores_mov
             - decklink_4k_h264_mxf
             - decklink_4k_prores
             - desktop_capture
             - audio_only'''))
             
        self.parser.add_argument("--clip", required=True, type=str, help="clip file to load (file location)")
        self.parser.add_argument("--mark_in", required=False, type=float, default=0, help="Mark in (second)")
        self.parser.add_argument("--length", required=False, type=float, default=0, help="Length (second)")
        self.parser.add_argument("--preset", required=False, type=str, default="vga_anything", help="Load pipeline preset(default : vga_anything)")
        self.parser.add_argument("--auto_start", required=False, type=int, default=0, help="start with playing (0 or 1)")
        self.parser.add_argument("--decklink",  required=False, type=int, default=0, help="Decklink number, start from 0 (default 0)")
        self.parser.add_argument("--port_report",  required=False, type=int, default=5253, help="UDP port for progress report (default 5253)")
        self.parser.add_argument("--port_command",  required=False, type=int, default=5250, help="TCP port for command reception (default 5250)")
        self.parser.add_argument("--guid",  required=False, type=str, default="", help="audio_only preset --> select audio output device by GUID")
        self.parser.add_argument("--udp_osc_rate",  required=False, type=int, default=1, help="UDP report interval (default 1, Less report with large value)")

    
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
            clientsocket.send(("tcp MSG // " + data + " ").encode())
            do_tcp_command(data)
        except:
            updatelog("Error while TCP receive or send")
            

def do_preview():
    global player, gui
    gui.layout.put(player.element["sink_gtk"].get_property("widget"), 700, 8)
    player.element["sink_gtk"].props.widget.set_size_request(160,90)



def do_show_waveform():
    global wave_gui, player
    
    wave_gui.load_png()
    wave_gui.set_new_title("Waveform viewer by sendust // " + player.clip)

    
    

class subprocess_run():
    thread = ""
    pid = 0
    process = ""
    progress = {}
    
    def __init__(self):
        self.thread = ""
        self.pid = 0
    
    def run(self, cmd):
        print(">>>   start subprocess with cmd ", cmd)
        self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        self.pid = self.process.pid
        self.thread = threading.Thread(target=self.readstdout, args=[self.process], daemon=True)
        self.thread.start()
        print(">>>   Sub process started with pid ", self.pid)
        
    def readstdout(self, p):
        while True:
            try:
                line = p.stdout.readline()
            except:
                print("Error read stdout")
            if not line:
                break
            text = line.strip()
            print("[{0:.4f}]".format(time.perf_counter()), end="  ")
            if not self.parser(text):
                print(text)

        try:
            out, errs = p.communicate(timeout=2)
        except TimeoutExpired:
            p.kill()
            out, errs = p.communicate()
        print("Finish out >>" , out)
        print("Finish error >>" , errs)            
        print("Finish read stdout... Terminating new thread...../////////////////////////")
        do_show_waveform()
        
    def parser(self, text):
        tuple_startwith = ("frame=", "fps=", "bitrate=", "total_size=", "out_time_ms=", "out_time=", "dup_frames=", "drop_frames=", "speed=", "progress=")
        for item in tuple_startwith:
            if text.startswith(item):
                key_val = text.split("=")
                try:
                    self.progress[key_val[0]] = key_val[1].strip()
                    print(self.progress)
                    return True
                except:
                    print("Error parsing progress output")
        return False
                    


    def send_break(self):
        print("Sending break signal")
        try:
            self.process.send_signal(signal.CTRL_BREAK_EVENT)
        except:
            print("Error send break signal")    

    def send_q(self):
        print("Sending q character to stdin")
        try:
            self.process.communicate(input="q")
        except:
            print("Error write stdin q")    

        


            

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

    
gui = mywindow()
gui.move_primary(5, 5)
debug_gui = debugwindow()
debug_gui.show_gui()


args_list = cli_parser()
args_list.print_args()


osc = osc_like_udp_info("127.0.0.1", args_list.args.port_report)  # default 5253
osc.set_udp_interval(args_list.args.udp_osc_rate)                 # default 1
amcp = tcp_svr("127.0.0.1", args_list.args.port_command)          # default 5250

# New thread with daemon enables termination of thread while script quiet
threading.Thread(target=amcp.run_server, daemon=True).start()  ## Start amcp server with new thread
#threading.Timer(1, lambda: gui.maximize()).start()  ## Restore minimized GTK window



try:
    updatelog("Start gui loop -----------------------")
    do_load()
    if player.auto_start:
        player.play()
    else:
        player.pause()
    gui.show_gui()
    gui.run()


except KeyboardInterrupt:
    updatelog("Keyboard interrupt detected exit to system")
    Gtk.main_quit()
    sys.exit(0)
    
    
    
    
    