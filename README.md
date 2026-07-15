# Project-Akira
Local AI companion

## Set Up:
Python 3.10.11 was used creating this project, I recommend this version for compatability.
pip install -r requirements.txt
  
### Basic tts only
Download LM Studio and set up a local server  
IF Needed, make api key and put it llm.py  
run the project assistant.py  

### Add Voice Changer
Download Virtual Audio Cable  
Download https://github.com/w-okada/voice-changer  
run the project assistant.py, in windows audio settings, change python audio output to vb cable in  
in the voice changer, select vb cable out as audio source and set processing delay to 500ms.  
add voice changer profile if wanted.  

### Add animated model
Download VSeeFace and find or make vrm model. You can make a vrm model with vroid studio, it is free  
open VseeFace and import model and start  
go to settings and change osc/vmc reciever on  
Start assistant.py  
