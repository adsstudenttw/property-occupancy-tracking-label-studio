# High Street Property Occupancy Tracking Label Studio

Automatic MOTChallenge dataset generation from video annotations using Label
Studio OSS + Docker Compose + FFmpeg. Supports multiple videos, automatic
resolution detection, and produces MOTChallenge sequences ready for training:
- BoostTrack++
- MOTIP
- SiamMOT
Designed for use on SURF Research Cloud, Ubuntu 22.04 Server.



---

## 2. Quick-start - Labelling

1. Clone
`git clone
https://github.com/adsstudenttw/property-occupancy-tracking-label-studio`  
`cd property-occupancy-tracking-label-studio`

2. Environment setup  
Copy the example env file:  
`cp .env.example .env`  
Edit .env and paste your Label Studio API key:  
`LABEL_STUDIO_API_KEY=YOUR_KEY_HERE`  

3. Configure the project via config.yml  
`nano config.yml`  

4. Starting Label Studio OSS  
Launch services:  
`docker compose up -d`  
The service becomes reachable via SURF Research Cloud:  
- Go to VM → Access  
- Add an Application Endpoint  
- Protocol: HTTP  
- Port: 8080  
Open the provided URL  

5. Create Label Studio user & Get API Key  
Inside LS:  
- Create an admin account  
- Go to Account → Access Tokens  
- Generate an API TOKEN  
- Paste it into .env  
Example:  
`LABEL_STUDIO_API_KEY=7a0c5c43....`  
Restart exporter container:
`docker compose down`  
`docker compose up -d`  

6. Add videos to Label Studio  
Place your videos in:  
`videos/`  
or mount your SURF storage as:  
`./videos → /mnt/surf-storage`  

In Label Studio:  
- Create a project  
- Use Local Storage Import  
- Select /label-studio/files (this maps to your videos/ directory)  


7. Annotate videos at 1 FPS  
You have time constraints → annotate one frame per second, not all frames.  
Recommended workflow:  
- Skip through the timeline at 1-second intervals  
- Only label frames with changes  
- Track objects using LS’s video annotation interface  
Label Studio does not require every frame to be annotated.  

8. Run the automated MOT exporter  
When annotations are ready:  
`docker compose run --rm exporter`  
This will:  
- Fetch COCO export from Label Studio via API  
- Split COCO per video  
- Run ffprobe to detect:  
    - Width  
    - Height  
    - Source FPS  
- Extract frames at 1 FPS using FFmpeg  
- Convert annotations → MOT format  
- Build directories like:  
`data/mot_output/`  
`   seq01/`  
`      img1/000001.jpg`  
`      img1/000002.jpg`  
`      gt/gt.txt`  
`      seqinfo.ini`  
`seq02/`  
`      ...`  

9. What the output looks like  
`data/mot_output/`  
`└── video01/`  
`     ├── img1/`  
`     │    ├── 000001.jpg`  
`     │    ├── 000002.jpg`  
`     │    └── ...`  
`     ├── gt/`  
`     │    └── gt.txt`  
`     └── seqinfo.ini`  

10. Updating annotations & regenerating data  
If you update annotations:  
`docker compose run --rm exporter`  
The pipeline always refreshes outputs.  

11. Cleaning everything    
Stop containers:  
`docker compose down`  
Remove generated sequences:  
`rm -rf data/mot_output/*`  


---

## 3. Project Layout

```text
property-occupancy-tracking-label-studio/
│
├── docker-compose.yml
├── .env.example
├── config.yml
├── README.md
│
├── exporter/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── run_export.py
│
├── scripts/
│   ├── coco_to_mot_simple.py
│   └── split_coco_by_video.py
│
├── data/
│   ├── label-studio/         # (generated)
│   ├── postgres/             # (generated)
│   ├── exports/              # (generated)
│   └── mot_output/           # (generated MOT datasets)
│
└── videos/                   # Place your .mp4 videos here (or mount SURF storage)