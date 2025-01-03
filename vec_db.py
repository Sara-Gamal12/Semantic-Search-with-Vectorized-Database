from typing import Dict, List, Annotated
import numpy as np
import os
import struct
from sklearn.cluster import KMeans
from utils import *
import tqdm
import heapq
import shutil
import sys
from sklearn.cluster import MiniBatchKMeans

from itertools import chain
DB_SEED_NUMBER = 42
ELEMENT_SIZE = np.dtype(np.float32).itemsize
DIMENSION = 70

class VecDB:
    def __init__(self, database_file_path = "saved_db.dat", index_file_path = "index.dat", new_db = True, db_size = None) -> None:
        self.db_path = database_file_path
        self.index_path = index_file_path
        self.no_centroids=0

        if new_db:
            if db_size is None:
                raise ValueError("You need to provide the size of the database")
            # delete the old DB file if exists
            if os.path.exists(self.db_path):
                os.remove(self.db_path)
            self.generate_database(db_size)
     
    
    def generate_database(self, size: int) -> None:
        rng = np.random.default_rng(DB_SEED_NUMBER)
        vectors = rng.random((size, DIMENSION), dtype=np.float32)
        self._write_vectors_to_file(vectors)
        self._build_index()

    def _write_vectors_to_file(self, vectors: np.ndarray) -> None:
        mmap_vectors = np.memmap(self.db_path, dtype=np.float32, mode='w+', shape=vectors.shape)
        mmap_vectors[:] = vectors[:]
        mmap_vectors.flush()

    def _get_num_records(self) -> int:
        return os.path.getsize(self.db_path) // (DIMENSION * ELEMENT_SIZE)

    def insert_records(self, rows: Annotated[np.ndarray, (int, 70)]):
        num_old_records = self._get_num_records()
        num_new_records = len(rows)
        full_shape = (num_old_records + num_new_records, DIMENSION)
        mmap_vectors = np.memmap(self.db_path, dtype=np.float32, mode='r+', shape=full_shape)
        mmap_vectors[num_old_records:] = rows
        mmap_vectors.flush()
        #TODO: might change to call insert in the index, if you need
        self._build_index()

    def get_one_row(self, row_num: int) -> np.ndarray:
        # This function is only load one row in memory
        try:
            offset = row_num * DIMENSION * ELEMENT_SIZE
            mmap_vector = np.memmap( self.db_path, dtype=np.float32, mode='r', shape=(1, DIMENSION), offset=offset)
            return np.array(mmap_vector[0])
        except Exception as e:
            return f"An error occurred: {e}"
        
   
    def get_rows(self, ids) -> np.ndarray:
        try:
            # Sort the IDs for efficient processing
            vectors = np.empty((len(ids), DIMENSION), dtype=np.float32)
            with open(self.db_path, "rb") as file:
                # Group IDs into continuous ranges
                ranges = []
                start = ids[0]
                prev = ids[0]
                for id in ids[1:]:
                    if id == prev + 1:  # Extend the current range
                        prev = id
                    else:  # Start a new range
                        ranges.append((start, prev))
                        start = id
                        prev = id
                ranges.append((start, prev))  # Add the last range
                # Read each range in a single I/O operation
                vector_idx = 0
                for start, end in ranges:
                    range_size = end - start + 1
                    offset = start * DIMENSION * ELEMENT_SIZE
                    file.seek(offset)
                    # Read the entire block of vectors for this range
                    block_data = file.read(range_size * DIMENSION * ELEMENT_SIZE)
                    block_vectors = np.frombuffer(block_data, dtype=np.float32).reshape(-1, DIMENSION)
                    # Assign the block vectors to the appropriate locations in the output array
                    for i in range(range_size):
                        vectors[vector_idx] = block_vectors[i]
                        vector_idx += 1 
                    del block_data, block_vectors
                del ranges               
        except Exception as e:
            print(f"Error while reading vectors: {e}")
            return np.empty((0, DIMENSION), dtype=np.float32)  # Return an empty array on error
        return vectors

    def _vectorized_cal_score(self, vec1, vec2):

        # Calculate the dot product between each vector in vec1 and the broadcasted vec2
        # dot_product = np.sum(vec1 * vec2_broadcasted, axis=1)
        # Calculate the dot product between each vector in vec1 and vec2
        dot_product = np.dot(vec1, vec2.T)

        # Calculate the norm of each vector in vec1
        norm_vec1 = np.linalg.norm(vec1, axis=1)

        # Calculate the norm of vec2
        norm_vec2 = np.linalg.norm(vec2)

        # Calculate the cosine similarity for each pair of vectors
        cosine_similarity = dot_product / (norm_vec1 * norm_vec2)

        return cosine_similarity.squeeze()


    def get_all_rows(self) -> np.ndarray:
        # Take care this load all the data in memory
        num_records = self._get_num_records()
        vectors = np.memmap(self.db_path, dtype=np.float32, mode='r', shape=(num_records, DIMENSION))
        return np.array(vectors)
    
    
    
    def retrieve(self, query: Annotated[np.ndarray, (1, DIMENSION)], top_k = 5):
        n_probs = 5
        if self._get_num_records() <= 10*10**6:
            n_probs = 12
        elif self._get_num_records() == 15 * 10**6:
            n_probs = 10
        

        top_centroids = self._get_top_centroids(query, n_probs)
        results = []

        query_norm = np.linalg.norm(query)
        query_squeezed = query.squeeze()
        ids=[]
        for centroid in top_centroids:
            #insert item to flatten list
            ids.append(read_file_records_mmap(self.index_path + "/" + str(centroid[1]) + ".bin"))
        del top_centroids
        ids=list(chain.from_iterable(ids))
        # ids =ids.flatten()
        data = np.array(self.get_rows(np.array(ids)))
         
        dot_products = np.dot(data, query_squeezed)
        norms_data = np.linalg.norm(data, axis=1)
        del data
        scores = dot_products / (norms_data * query_norm)
        results = list(zip(scores, ids))
        # for score, id in zip(scores, ids):
        #     heapq.heappush(results, (score, id))
        #     if len(results) > top_k:
        #         heapq.heappop(results)
        results = heapq.nlargest(top_k, results, key=lambda x: x[0])
        results = [result[1] for result in results]
        return results

    
    def _cal_score(self, vec1, vec2):
        dot_product = np.dot(vec1, vec2)
        norm_vec1 = np.linalg.norm(vec1)
        norm_vec2 = np.linalg.norm(vec2)
        cosine_similarity = dot_product / (norm_vec1 * norm_vec2)
        return cosine_similarity

    def _build_index(self):
      
        self.no_centroids = int(np.sqrt(self._get_num_records()))*2
        if(self._get_num_records()==15*10**6):
            self.no_centroids = int(np.sqrt(self._get_num_records()))*5
        if(self._get_num_records()==20*10**6):
            self.no_centroids = int(np.sqrt(self._get_num_records()))*6
    
        # chuck_size = min(10**8,self._get_num_records())
        training_data=self.get_all_rows()  
        kmeans = MiniBatchKMeans(n_clusters=self.no_centroids, random_state=0 , n_init = 3,batch_size=10**4 )
        # Fit the model
        kmeans.fit(training_data)
        
        labels=kmeans.predict(self.get_all_rows())
        centroids= kmeans.cluster_centers_
        #save centroids in file
        if os.path.exists(self.index_path):
            shutil.rmtree(self.index_path)
        os.makedirs(self.index_path, exist_ok=True)
       
        unique_labels = np.unique(labels)
      
        for label in tqdm.tqdm(unique_labels):
            indices = np.where(labels == label)[0]
            for index in (indices):
                write_file_records(self.index_path + "/" + str(label) + ".bin",  index)       
        write_file_centroids(self.index_path+"/centroids.bin",centroids)
          


            
    def _get_top_centroids(self, query, k):
          # Find the nearest centroids to the query
          centroids_data = read_file_centroids_with_memap(self.index_path + "/centroids.bin")
          # Initialize a heap to store the centroids and their scores
          heap = []
          
          # Iterate over the centroids data, which contains (offset, size, centroid)
          centroids = np.array([centroid for centroid in centroids_data])
          scores = self._vectorized_cal_score(centroids, query.squeeze())
          heap = list(zip(scores, range(len(centroids_data))))
          top_centroids = heapq.nlargest(k, heap)
          return top_centroids
    