from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from typing import Dict, List, Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger(__name__)

class JobDocument(BaseModel):
    job_id: str
    status: str  # "processing", "completed", "failed"
    video_filename: str
    video_size: int
    video_content_type: str
    temp_dir: str
    video_path: str
    analyses: List[str]
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    processing_start_time: Optional[datetime] = None
    processing_end_time: Optional[datetime] = None
    # TwelveLabs specific fields
    twelve_labs_video_id: Optional[str] = None
    twelve_labs_index_id: Optional[str] = None
    twelve_labs_task_id: Optional[str] = None
    indexing_status: Optional[str] = None  # "pending", "queued", "indexing", "ready", "failed"
    indexing_progress: Optional[float] = None  # 0.0 to 1.0
    indexing_start_time: Optional[datetime] = None
    indexing_end_time: Optional[datetime] = None

class ResultDocument(BaseModel):
    job_id: str
    analysis_type: str
    results: Dict[str, Any]
    processing_time: float
    created_at: datetime
    updated_at: Optional[datetime] = None

class DatabaseConnection:
    def __init__(self, config):
        self.config = config
        self._client = None
        self._db = None
        
    def connect(self):
        """Establish MongoDB connection with error handling"""
        try:
            self._client = MongoClient(
                self.config.MONGODB_URI,
                serverSelectionTimeoutMS=5000,  # 5 second timeout
                connectTimeoutMS=5000,
                socketTimeoutMS=5000
            )
            # Test connection
            self._client.admin.command('ping')
            self._db = self._client[self.config.MONGODB_DB]
            self._ensure_indexes()
            logger.info(f"Connected to MongoDB: {self.config.MONGODB_DB}")
            return True
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            logger.error(f"Failed to connect to MongoDB: {e}")
            return False
            
    def _ensure_indexes(self):
        """Create required indexes"""
        jobs_col = self._db["jobs"]
        results_col = self._db["results"]
        
        # Jobs collection indexes
        jobs_col.create_index("job_id", unique=True)
        jobs_col.create_index("status")
        jobs_col.create_index("created_at")
        jobs_col.create_index("twelve_labs_video_id")
        jobs_col.create_index("twelve_labs_index_id")
        jobs_col.create_index("indexing_status")
        jobs_col.create_index("video_filename")  # For duplicate detection
        
        # Results collection indexes  
        results_col.create_index("job_id")
        results_col.create_index("analysis_type")
        results_col.create_index([("job_id", 1), ("analysis_type", 1)], unique=True)
        
    @property
    def db(self):
        if self._db is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._db
        
    @property
    def jobs_collection(self):
        return self.db["jobs"]
        
    @property
    def results_collection(self):
        return self.db["results"]
        
    def close(self):
        if self._client:
            self._client.close()
            
class JobManager:
    def __init__(self, db_connection: DatabaseConnection):
        self.db = db_connection
        
    def create_job(self, job_data: Dict[str, Any]) -> bool:
        """Create a new job in MongoDB"""
        try:
            job_doc = JobDocument(
                job_id=job_data["job_id"],
                status="processing",
                video_filename=job_data["video_filename"],
                video_size=job_data["video_size"],
                video_content_type=job_data["video_content_type"],
                temp_dir=job_data["temp_dir"],
                video_path=job_data["video_path"],
                analyses=job_data["analyses"],
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                processing_start_time=datetime.utcnow()
            )
            
            self.db.jobs_collection.insert_one(job_doc.dict())
            logger.info(f"Created job {job_data['job_id']}")
            return True
        except Exception as e:
            logger.error(f"Failed to create job {job_data['job_id']}: {e}")
            return False
            
    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job by ID"""
        try:
            job = self.db.jobs_collection.find_one({"job_id": job_id})
            if job:
                # Remove MongoDB's _id field
                job.pop("_id", None)
            return job
        except Exception as e:
            logger.error(f"Failed to get job {job_id}: {e}")
            return None
            
    def update_job_status(self, job_id: str, status: str, error: Optional[str] = None) -> bool:
        """Update job status"""
        try:
            update_data = {
                "status": status,
                "updated_at": datetime.utcnow()
            }
            
            if status == "completed" or status == "failed":
                update_data["processing_end_time"] = datetime.utcnow()
                
            if error:
                update_data["error"] = error
                
            result = self.db.jobs_collection.update_one(
                {"job_id": job_id},
                {"$set": update_data}
            )
            
            if result.modified_count > 0:
                logger.info(f"Updated job {job_id} status to {status}")
                return True
            else:
                logger.warning(f"No job found to update: {job_id}")
                return False
        except Exception as e:
            logger.error(f"Failed to update job {job_id}: {e}")
            return False
            
    def delete_job(self, job_id: str) -> bool:
        """Delete job from database"""
        try:
            result = self.db.jobs_collection.delete_one({"job_id": job_id})
            if result.deleted_count > 0:
                logger.info(f"Deleted job {job_id}")
                return True
            else:
                logger.warning(f"No job found to delete: {job_id}")
                return False
        except Exception as e:
            logger.error(f"Failed to delete job {job_id}: {e}")
            return False
    
    def get_video_by_filename(self, filename: str) -> Optional[Dict[str, Any]]:
        """Get job with TwelveLabs video_id by filename"""
        try:
            job = self.db.jobs_collection.find_one({
                "video_filename": filename,
                "twelve_labs_video_id": {"$ne": None}
            })
            if job:
                job.pop("_id", None)
            return job
        except Exception as e:
            logger.error(f"Failed to get video by filename {filename}: {e}")
            return None

    def update_twelve_labs_metadata(self, job_id: str, video_id: str = None, index_id: str = None, 
                                   task_id: str = None, indexing_status: str = None, 
                                   indexing_progress: float = None) -> bool:
        """Update TwelveLabs specific metadata for a job"""
        try:
            update_data = {
                "updated_at": datetime.utcnow()
            }
            
            if video_id:
                update_data["twelve_labs_video_id"] = video_id
            if index_id:
                update_data["twelve_labs_index_id"] = index_id
            if task_id:
                update_data["twelve_labs_task_id"] = task_id
            if indexing_status:
                update_data["indexing_status"] = indexing_status
                # Set timing fields based on status
                if indexing_status in ["pending", "queued", "validating", "running", "indexing"] and not self.get_job(job_id).get("indexing_start_time"):
                    update_data["indexing_start_time"] = datetime.utcnow()
                elif indexing_status in ["ready", "failed"]:
                    update_data["indexing_end_time"] = datetime.utcnow()
            if indexing_progress is not None:
                update_data["indexing_progress"] = indexing_progress
                
            result = self.db.jobs_collection.update_one(
                {"job_id": job_id},
                {"$set": update_data}
            )
            
            if result.modified_count > 0:
                logger.info(f"Updated TwelveLabs metadata for job {job_id}")
                return True
            else:
                logger.warning(f"No job found to update: {job_id}")
                return False
        except Exception as e:
            logger.error(f"Failed to update TwelveLabs metadata for job {job_id}: {e}")
            return False
            
class ResultsManager:
    def __init__(self, db_connection: DatabaseConnection):
        self.db = db_connection
        
    def store_results(self, job_id: str, analysis_type: str, results: Dict[str, Any], processing_time: float) -> bool:
        """Store analysis results"""
        try:
            now = datetime.utcnow()
            result_doc = ResultDocument(
                job_id=job_id,
                analysis_type=analysis_type,
                results=results,
                processing_time=processing_time,
                created_at=now,
                updated_at=now
            )

            payload = result_doc.dict()
            created_at = payload.pop("created_at")
            self.db.results_collection.update_one(
                {"job_id": job_id, "analysis_type": analysis_type},
                {"$set": payload, "$setOnInsert": {"created_at": created_at}},
                upsert=True
            )
            logger.info(f"Stored results for job {job_id}, analysis: {analysis_type}")
            return True
        except Exception as e:
            logger.error(f"Failed to store results for job {job_id}: {e}")
            return False
            
    def get_results(self, job_id: str) -> List[Dict[str, Any]]:
        """Get all results for a job"""
        try:
            results = list(self.db.results_collection.find({"job_id": job_id}))
            # Remove MongoDB's _id field from each result
            for result in results:
                result.pop("_id", None)
            return results
        except Exception as e:
            logger.error(f"Failed to get results for job {job_id}: {e}")
            return []
            
    def delete_results(self, job_id: str) -> bool:
        """Delete all results for a job"""
        try:
            result = self.db.results_collection.delete_many({"job_id": job_id})
            logger.info(f"Deleted {result.deleted_count} results for job {job_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete results for job {job_id}: {e}")
            return False

def get_mongodb_connection(config):
    """Get MongoDB connection and collection (legacy compatibility)"""
    client = MongoClient(config.MONGODB_URI)
    db = client[config.MONGODB_DB]
    videos_col = db["videos"]
    return db, videos_col 
