# HTTP wrapper for Simod 5.1.6
# This is adapted to work with SimuBridge while using Simod 5.1.6

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from uuid import uuid4

import pandas as pd
import uvicorn
from fastapi import FastAPI, BackgroundTasks, Form, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from enum import Enum


# Configuration
DEBUG = os.environ.get('SIMOD_HTTP_DEBUG', 'false').lower() == 'true'
STORAGE_PATH = Path(os.environ.get('SIMOD_HTTP_STORAGE_PATH', '/tmp/simod'))
STORAGE_PATH.mkdir(parents=True, exist_ok=True)

# FastAPI app
app = FastAPI(title="Simod HTTP API for Simod 5.1.6")

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Models
class RequestStatus(str, Enum):
    UNKNOWN = "unknown"
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"


class DiscoveryRequest(BaseModel):
    id: str
    status: RequestStatus
    timestamp: Optional[pd.Timestamp] = None
    output_dir: Optional[Path] = None

    class Config:
        arbitrary_types_allowed = True


class DiscoveryResponse(BaseModel):
    request_id: str
    request_status: RequestStatus
    archive_url: Optional[str] = None


# Request storage (simple file-based for backward compatibility)
def save_request(request: DiscoveryRequest):
    """Save request metadata to disk"""
    request_dir = STORAGE_PATH / 'requests' / request.id
    request_dir.mkdir(parents=True, exist_ok=True)
    
    import json
    with open(request_dir / 'request.json', 'w') as f:
        json.dump({
            'id': request.id,
            'status': request.status.value,
            'timestamp': request.timestamp.isoformat() if request.timestamp else None,
            'output_dir': str(request.output_dir) if request.output_dir else None
        }, f)


def load_request(request_id: str) -> DiscoveryRequest:
    """Load request metadata from disk"""
    import json
    request_dir = STORAGE_PATH / 'requests' / request_id
    
    if not request_dir.exists():
        raise FileNotFoundError(f"Request {request_id} not found")
    
    with open(request_dir / 'request.json', 'r') as f:
        data = json.load(f)
    
    return DiscoveryRequest(
        id=data['id'],
        status=RequestStatus(data['status']),
        timestamp=pd.Timestamp(data['timestamp']) if data['timestamp'] else None,
        output_dir=Path(data['output_dir']) if data['output_dir'] else None
    )


# Background task to run Simod discovery
def run_simod_discovery(request_id: str, event_log_path: Path, configuration_path: Optional[Path]):
    """Run Simod discovery in background"""
    try:
        request = load_request(request_id)
        request.status = RequestStatus.RUNNING
        request.timestamp = pd.Timestamp.now()
        save_request(request)
        
        output_dir = STORAGE_PATH / 'requests' / request_id / 'output'
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Build Simod command - use poetry run to access Simod in its virtual environment
        cmd = ['bash', '-c', f'cd /usr/src/Simod && poetry run simod']
        
        # Add event log parameter  
        if configuration_path:
            cmd = ['bash', '-c', f'cd /usr/src/Simod && poetry run simod --configuration {configuration_path} --output {output_dir}']
        else:
            cmd = ['bash', '-c', f'cd /usr/src/Simod && poetry run simod --one-shot --event-log {event_log_path} --output {output_dir}']
        
        logging.info(f"Running Simod command: {' '.join(cmd)}")
        
        # Run Simod
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )
        
        logging.info(f"Simod stdout: {result.stdout}")
        if result.stderr:
            logging.error(f"Simod stderr: {result.stderr}")
        
        # Update request status based on result
        request = load_request(request_id)
        if result.returncode == 0:
            request.status = RequestStatus.SUCCESS
            request.output_dir = output_dir
        else:
            request.status = RequestStatus.FAILURE
            logging.error(f"Simod failed with return code {result.returncode}")
        
        request.timestamp = pd.Timestamp.now()
        save_request(request)
        
    except Exception as e:
        logging.error(f"Error running Simod discovery: {e}", exc_info=True)
        try:
            request = load_request(request_id)
            request.status = RequestStatus.FAILURE
            request.timestamp = pd.Timestamp.now()
            save_request(request)
        except Exception as save_error:
            logging.error(f"Error saving failure status: {save_error}")


# Routes
@app.post('/discoveries')
async def create_discovery(
    background_tasks: BackgroundTasks,
    event_log: UploadFile,
    configuration: Optional[UploadFile] = None,
    callback_url: Optional[str] = None,
) -> JSONResponse:
    """
    Create a new business process model discovery request.
    """
    # Create new request
    request_id = str(uuid4())
    request = DiscoveryRequest(
        id=request_id,
        status=RequestStatus.ACCEPTED
    )
    
    request_dir = STORAGE_PATH / 'requests' / request_id
    request_dir.mkdir(parents=True, exist_ok=True)
    
    # Save event log
    event_log_extension = _infer_event_log_extension(event_log.content_type, event_log.filename)
    event_log_path = request_dir / f'event_log{event_log_extension}'
    
    with open(event_log_path, 'wb') as f:
        content = await event_log.read()
        f.write(content)
    
    # Save configuration if provided
    configuration_path = None
    if configuration:
        config_extension = '.yaml' if 'yaml' in configuration.content_type.lower() else '.json'
        configuration_path = request_dir / f'configuration{config_extension}'
        
        with open(configuration_path, 'wb') as f:
            content = await configuration.read()
            f.write(content)
    
    # Save request metadata
    save_request(request)
    
    # Start background task
    background_tasks.add_task(
        run_simod_discovery,
        request_id,
        event_log_path,
        configuration_path
    )
    
    response = DiscoveryResponse(
        request_id=request_id,
        request_status=request.status
    )
    
    return JSONResponse(
        status_code=202,
        content=response.model_dump()
    )


@app.get('/discoveries/{request_id}')
async def read_discovery(request_id: str) -> DiscoveryResponse:
    """
    Get the status of a discovery request.
    """
    try:
        request = load_request(request_id)
        
        # Build archive URL if successful
        archive_url = None
        if request.status == RequestStatus.SUCCESS and request.output_dir:
            # Look for result files
            best_result_dir = request.output_dir / 'best_result'
            if best_result_dir.exists():
                # Create a simple list of available files
                archive_url = f"/discoveries/{request_id}/results.tar.gz"
        
        return DiscoveryResponse(
            request_id=request_id,
            request_status=request.status,
            archive_url=archive_url
        )
    except FileNotFoundError:
        return JSONResponse(
            status_code=404,
            content={
                "request_id": request_id,
                "request_status": RequestStatus.UNKNOWN.value,
                "message": "Request not found"
            }
        )


@app.get('/discoveries/{request_id}/{file_name}')
async def read_discovery_file(request_id: str, file_name: str):
    """
    Get a file from a discovery request.
    """
    try:
        request = load_request(request_id)
        
        if not request.output_dir or not request.output_dir.exists():
            return JSONResponse(
                status_code=404,
                content={"message": "Output not found"}
            )
        
        # Handle archive request
        if file_name == 'results.tar.gz':
            best_result_dir = request.output_dir / 'best_result'
            if not best_result_dir.exists():
                return JSONResponse(
                    status_code=404,
                    content={"message": "Results not found"}
                )
            
            # Create tar.gz archive
            archive_path = request.output_dir / 'results'
            shutil.make_archive(str(archive_path), 'gztar', best_result_dir)
            
            with open(f"{archive_path}.tar.gz", 'rb') as f:
                content = f.read()
            
            return Response(
                content=content,
                media_type='application/gzip',
                headers={
                    'Content-Disposition': f'attachment; filename="results.tar.gz"'
                }
            )
        
        # Handle individual file request
        file_path = request.output_dir / 'best_result' / file_name
        if not file_path.exists():
            # Try without best_result subdirectory
            file_path = request.output_dir / file_name
            if not file_path.exists():
                return JSONResponse(
                    status_code=404,
                    content={"message": f"File not found: {file_name}"}
                )
        
        media_type = _infer_media_type(file_name)
        
        with open(file_path, 'rb') as f:
            content = f.read()
        
        return Response(
            content=content,
            media_type=media_type,
            headers={
                'Content-Disposition': f'attachment; filename="{file_name}"'
            }
        )
    
    except FileNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"message": "Request not found"}
        )


# Helper functions
def _infer_event_log_extension(content_type: str, filename: str) -> str:
    """Infer file extension from content type or filename"""
    if 'csv' in content_type.lower() or (filename and filename.endswith('.csv')):
        return '.csv'
    elif 'xml' in content_type.lower() or (filename and filename.endswith('.xes')):
        return '.xes'
    elif filename and filename.endswith('.xml'):
        return '.xml'
    else:
        return '.csv'  # Default


def _infer_media_type(file_name: str) -> str:
    """Infer media type from file extension"""
    if file_name.endswith('.csv'):
        return 'text/csv'
    elif file_name.endswith('.xml') or file_name.endswith('.xes') or file_name.endswith('.bpmn'):
        return 'application/xml'
    elif file_name.endswith('.json'):
        return 'application/json'
    elif file_name.endswith('.png'):
        return 'image/png'
    elif file_name.endswith('.jpg') or file_name.endswith('.jpeg'):
        return 'image/jpeg'
    elif file_name.endswith('.pdf'):
        return 'application/pdf'
    elif file_name.endswith('.txt'):
        return 'text/plain'
    elif file_name.endswith('.gz'):
        return 'application/gzip'
    elif file_name.endswith('.tar'):
        return 'application/tar'
    else:
        return 'application/octet-stream'


# Startup logging
@app.on_event('startup')
async def startup():
    logging.basicConfig(
        level=logging.DEBUG if DEBUG else logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logging.info("Starting Simod HTTP API for Simod 5.1.6")
    logging.info(f"Storage path: {STORAGE_PATH}")


if __name__ == '__main__':
    uvicorn.run(
        'main_v2:app',
        host='0.0.0.0',
        port=80,
        log_level='info' if not DEBUG else 'debug',
    )
