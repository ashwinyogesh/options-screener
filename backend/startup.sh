#!/bin/bash
source /home/site/wwwroot/antenv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000
