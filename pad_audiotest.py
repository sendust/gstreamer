# Gstreamer with python
# element, pad manipulation, get audio level data
# Useful Debug environment setup, print pad information... etc
# Message handling.
# Audio multi channel manipulation
# Caps filter handling
#
#
#
#
#
#
# Code managed by sendust   2021/11/12
#



import gi, threading, time, sys
import os, urllib.parse

gi.require_version('Gst', '1.0')
gi.require_version('Gtk', '3.0')

from gi.repository import Gst, Gtk, GLib, Gdk


os.environ["GST_DEBUG_DUMP_DOT_DIR"] = "z:\\ahk\\gstreamer\\graph\\"
os.putenv('GST_DEBUG_DUMP_DIR_DIR', "z:\\ahk\\gstreamer\\graph\\")



def print_field(field, value, pfx):
    str = Gst.value_serialize(value)
    print("{0:s}  {1:15s}: {2:s}".format(
        pfx, GLib.quark_to_string(field), str))
    return True


def print_caps(caps, pfx):
    if not caps:
        return

    if caps.is_any():
        print("{0:s}ANY".format(pfx))
        return

    if caps.is_empty():
        print("{0:s}EMPTY".format(pfx))
        return

    for i in range(caps.get_size()):
        structure = caps.get_structure(i)
        print("{0:s}{1:s}".format(pfx, structure.get_name()))
        structure.foreach(print_field, pfx)

# prints information about a pad template (including its capabilities)


def print_pad_templates_information(factory):
    print("Pad templates for {0:s}".format(factory.get_name()))
    if factory.get_num_pad_templates() == 0:
        print("  none")
        return

    pads = factory.get_static_pad_templates()
    for pad in pads:
        padtemplate = pad.get()

        if pad.direction == Gst.PadDirection.SRC:
            print("  SRC template:", padtemplate.name_template)
        elif pad.direction == Gst.PadDirection.SINK:
            print("  SINK template:", padtemplate.name_template)
        else:
            print("  UNKNOWN template:", padtemplate.name_template)

        if padtemplate.presence == Gst.PadPresence.ALWAYS:
            print("    Availability: Always")
        elif padtemplate.presence == Gst.PadPresence.SOMETIMES:
            print("    Availability: Sometimes")
        elif padtemplate.presence == Gst.PadPresence.REQUEST:
            print("    Availability: On request")
        else:
            print("    Availability: UNKNOWN")

        if padtemplate.get_caps():
            print("    Capabilities:")
            print_caps(padtemplate.get_caps(), "      ")

        print("")

# shows the current capabilities of the requested pad in the given element


def print_pad_capabilities(element, pad_name):
    # retrieve pad
    pad = element.get_static_pad(pad_name)
    if not pad:
        print("ERROR: Could not retrieve pad '{0:s}'".format(pad_name))
        return

    # retrieve negotiated caps (or acceptable caps if negotiation is not
    # yet finished)
    caps = pad.get_current_caps()
    if not caps:
        caps = pad.get_allowed_caps()

    # print
    print("Caps for the {0:s} pad:".format(pad_name))
    print_caps(caps, "      ")


def on_message(bus, message):
    #print("Message received -> {}".format(message))
    mtype = message.type
    
    if mtype == Gst.MessageType.EOS:
        # Handle End of Stream
        print("End of stream")
        loop.quit()
    elif mtype == Gst.MessageType.ERROR:
        # Handle Errors
        err, debug = message.parse_error()
        print(err, debug)
        loop.quit()
    elif mtype == Gst.MessageType.WARNING:
        # Handle warnings
        err, debug = message.parse_warning()
        print(err, debug)
    elif mtype == Gst.MessageType.ELEMENT:
        # Handle element message
        element_source = message.src.get_name()
        #print("Element message received, source is ---->  " + element_source)
        if element_source == "audio_level":
            structure_message = message.get_structure()
            #print(structure_message)      # Display full message structure
            #print(structure_message.get_name())
            rms_value = structure_message.get_value("rms")
            end_time =  structure_message.get_value("endtime")
            timestamp =  structure_message.get_value("timestamp")
            stream_time =  structure_message.get_value("stream-time")
            running_time =  structure_message.get_value("running-time")
            duration_time =  structure_message.get_value("duration")
            #print(rms_value)
            print(" end={0}, timestamep={1}, stream={2}, run={3}, duration={4}, level is {5}".format(end_time, timestamp, stream_time, running_time, duration_time, rms_value), end="\r")

    
    elif mtype == Gst.MessageType.STATE_CHANGED:    
        old_state, new_state, pending_state = message.parse_state_changed()
        print("Pipeline state changed from '{0:s}' to '{1:s}'".format(
            Gst.Element.state_get_name(old_state),
            Gst.Element.state_get_name(new_state)))

    
    else:
      print("Message")

    return True
    
    
    
    
def on_level_message(bus, message):
    print("Level Message received -> {}".format(message))
    return True
    


Gst.init(None)

pipeline = Gst.Pipeline.new("test-pipeline")
bus = pipeline.get_bus()
bus.add_signal_watch()
bus.connect("message", on_message)
#bus.connect("level-message::element", on_level_message)




test_a1 = Gst.ElementFactory.make("audiotestsrc", "a1")
test_a2 = Gst.ElementFactory.make("audiotestsrc", "a2")

#test_a1.set_property("freq", 1000)
test_a1.set_property("wave", "ticks")

test_a2.set_property("freq", 1500)


caps1 = Gst.Caps.from_string("audio/x-raw, channels=(int)1, channel-mask=(bitmask)0x1")
filter1 = Gst.ElementFactory.make("capsfilter", "filter1")
filter1.set_property("caps", caps1)

caps2 = Gst.Caps.from_string("audio/x-raw, channels=(int)1, channel-mask=(bitmask)0x2")
filter2 = Gst.ElementFactory.make("capsfilter", "filter2")
filter2.set_property("caps", caps2)


print("Crteate filter src pad for connecting interleave")
filter_a1_pad = filter1.get_static_pad("src")
filter_a2_pad = filter2.get_static_pad("src")

sink =  Gst.ElementFactory.make("autoaudiosink", "asink")

audio_interleave = Gst.ElementFactory.make("interleave", "audio_muxer")
audio_resample =  Gst.ElementFactory.make("audioresample", "audio_resample")

level =  Gst.ElementFactory.make("level", "audio_level")


pipeline.add(test_a1)
pipeline.add(test_a2)
pipeline.add(audio_interleave)
pipeline.add(sink)
pipeline.add(filter1)
pipeline.add(filter2)
pipeline.add(audio_resample)
pipeline.add(level)



print("Create interleave sink pad")
pad_template_interleave = audio_interleave.get_pad_template("sink_%u")
audio_interleave_ch1_pad = audio_interleave.request_pad(pad_template_interleave, None, None)
audio_interleave_ch2_pad = audio_interleave.request_pad(pad_template_interleave, None, None)

print(
    "Obtained request pad {0} for audio interleave".format(
        audio_interleave_ch1_pad.get_name()))

print(
    "Obtained request pad {0} for audio interleave".format(
        audio_interleave_ch2_pad.get_name()))

print("Connect test audio src pad to interleave sink pad")
filter_a1_pad.link(audio_interleave_ch1_pad)
filter_a2_pad.link(audio_interleave_ch2_pad)

Gst.Element.link(test_a1, filter1)
Gst.Element.link(test_a2, filter2)

Gst.Element.link(audio_interleave, audio_resample)
Gst.Element.link(audio_resample, level)
Gst.Element.link(level, sink)

# Print debug dot file
Gst.debug_bin_to_dot_file(pipeline, Gst.DebugGraphDetails.CAPS_DETAILS, "python_dot_debug")

        
        
pipeline.set_state(Gst.State.PLAYING)

loop = GLib.MainLoop()              # for message callback

try:
    loop.run()
except KeyboardInterrupt:
    loop.quit()
    sys.exit(0)
