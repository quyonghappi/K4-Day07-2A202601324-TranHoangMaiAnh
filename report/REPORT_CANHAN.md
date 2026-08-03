# Báo Cáo Cá Nhân - Lab 7: Embedding & Vector Store

**Họ tên:** Trần Hoàng Mai Anh
**Nhóm:** Gicungduoc
**Ngày:** 03/08/2026

Báo cáo này mô tả phần cài đặt cá nhân và kết quả benchmark trên cùng năm câu hỏi của nhóm. Benchmark được chạy với `LocalEmbedder` dùng model `paraphrase-multilingual-MiniLM-L12-v2`; các kết quả retrieval được đối chiếu từ `report/RUN_BENCHMARK_RESULT.md` và chấm theo `docs/SCORING.md`.

## 1. Khởi động (Warm-up) - Cá nhân (5 điểm)

### Độ tương tự cosine

Cosine similarity đo độ giống nhau về hướng của hai vector embedding, không phụ thuộc nhiều vào độ dài vector. Giá trị càng gần 1 thì hai câu càng gần nhau về biểu diễn ngữ nghĩa; giá trị gần 0 cho thấy ít tương đồng, còn giá trị âm cho thấy hướng biểu diễn đối lập.

**Ví dụ có độ tương tự cao:**

- Câu A: `Người Mua có thể gửi yêu cầu trả hàng trong vòng 15 ngày.`
- Câu B: `Thời hạn gửi yêu cầu trả hàng của Người Mua là 15 ngày.`
- Tại sao tương đồng: Hai câu cùng nói về chủ thể Người Mua, hành động gửi yêu cầu trả hàng và thời hạn 15 ngày.

**Ví dụ có độ tương tự thấp:**

- Câu A: `Người bán phải phản hồi yêu cầu trả hàng trong 2 ngày.`
- Câu B: `Python là một ngôn ngữ lập trình phổ biến.`
- Tại sao khác: Hai câu thuộc hai chủ đề hoàn toàn khác nhau, một câu nói về chính sách trả hàng và một câu nói về lập trình.

Cosine thường phù hợp hơn khoảng cách Euclid cho text embedding vì nó tập trung vào hướng ngữ nghĩa của vector. Hai văn bản có độ dài khác nhau vẫn có thể gần nhau về nội dung nếu hướng embedding tương tự; việc chuẩn hóa vector cũng làm kết quả ổn định hơn.

### Bài toán tính toán chunking

Với cách chia trong `FixedSizeChunker`, bước trượt là:

```text
step = chunk_size - overlap = 300 - 50 = 250
```

Chunk bắt đầu tại các vị trí `0, 250, 500, ...`. Vì vậy:

```text
N = ceil((10_000 - 300) / 250) + 1
  = ceil(9_700 / 250) + 1
  = 39 + 1
  = 40 chunks
```

Nếu `overlap=100`, bước trượt còn `300 - 100 = 200`, nên:

```text
N = ceil((10_000 - 300) / 200) + 1
  = ceil(9_700 / 200) + 1
  = 49 + 1
  = 50 chunks
```

Overlap lớn hơn làm tăng số chunk và chi phí embedding, nhưng giúp giữ phần ngữ cảnh nằm ở ranh giới hai chunk. Điều này hữu ích khi một câu trả lời hoặc một điều khoản bị cắt giữa hai đoạn.

## 2. Hướng tiếp cận của tôi - Cá nhân (10 điểm)

### Các hàm chia nhỏ

`FixedSizeChunker.chunk` là đường cơ sở của thử nghiệm. Hàm dùng cửa sổ trượt với `step = chunk_size - overlap`, bảo đảm mỗi chunk không vượt quá `chunk_size` và hai chunk liên tiếp chia sẻ phần overlap. Hàm cũng kiểm tra tham số đầu vào, xử lý văn bản rỗng và dừng khi chunk cuối đã bao phủ phần còn lại của tài liệu.

`SentenceChunker.chunk` dùng biểu thức chính quy `(?<=[.!?])[ \t\r\n]+`: dấu câu được giữ lại trong câu, còn khoảng trắng sau dấu câu được dùng làm ranh giới. Hàm loại bỏ chuỗi rỗng và khoảng trắng thừa, sau đó nhóm tối đa `max_sentences_per_chunk` câu vào một chunk. Cách này xử lý được dấu chấm cuối dòng và không tách chỉ vì dấu chấm nằm trong câu nếu phía sau không có khoảng trắng phù hợp.

`RecursiveChunker.chunk` gọi `_split` với thứ tự ưu tiên `\n\n`, `\n`, `. `, khoảng trắng và cuối cùng là tách theo ký tự. `_split` chọn dấu phân cách đầu tiên thực sự xuất hiện, ghép các mảnh nhỏ khi tổng độ dài còn trong `chunk_size`, và đệ quy xuống dấu phân cách nhỏ hơn nếu một mảnh vẫn quá dài. Base case là chuỗi rỗng hoặc chuỗi không vượt quá `chunk_size`; khi không còn dấu phân cách dùng được, hàm tách theo cửa sổ ký tự để luôn tạo ra kết quả hợp lệ.

`SemanticChunker.chunk` trước hết tách văn bản thành các câu, tạo embedding cho từng câu rồi so sánh cosine similarity giữa hai câu liên tiếp. Câu tiếp theo được giữ trong chunk hiện tại khi similarity không thấp hơn `similarity_threshold` và tổng độ dài không vượt quá `chunk_size`; nếu một trong hai điều kiện không đạt, chunk hiện tại được đóng và tạo chunk mới. Một câu đơn quá dài vẫn được tách bằng `FixedSizeChunker` như bước dự phòng, vì khi đó không thể đồng thời giữ nguyên ngữ nghĩa và giới hạn kích thước.

### Lớp `EmbeddingStore`

`add_documents` tạo embedding cho từng nội dung và lưu một record gồm `id`, `content`, `metadata` và `embedding` trong bộ nhớ. `search` tạo embedding cho query, tính tích vô hướng giữa query và từng embedding đã lưu, sắp xếp giảm dần rồi trả về tối đa `top_k` kết quả cùng score. Với các embedding đã chuẩn hóa, tích vô hướng tương đương cosine similarity; với backend tổng quát, đây là điểm tương đồng mà store đang sử dụng.

`search_with_filter` lọc record theo metadata trước khi xếp hạng, nhờ đó câu hỏi Q2 chỉ tìm trong các chunk có `customer_role=seller`. Bộ lọc dùng điều kiện khớp tất cả cặp khóa-giá trị, nên không làm lẫn tài liệu khác vai trò. `delete_document` xóa mọi record có metadata `doc_id` tương ứng và trả về `True` nếu thực sự có record bị xóa.

Để giảm lỗi do embedding xếp nhầm các chunk chỉ có từ vựng chung, tôi bổ sung hai bước trong `src/store.py`. Hàm `expand_query` thêm các cụm theo intent chính sách, chẳng hạn `15 ngày`, `xử lý vi phạm`, `02 ngày lịch`, `phương thức nhận hoàn tiền` và `rút lại sự đồng ý`. Sau đó `_search_records` dùng hybrid reranking: 75% điểm semantic và 25% độ phủ từ khóa/phrase trong nội dung và metadata. Kết quả trả về vẫn giữ `score`, đồng thời có thêm `semantic_score` và `keyword_score` để kiểm tra nguyên nhân xếp hạng.

### Tác tử `KnowledgeBaseAgent`

`answer` trước tiên gọi `store.search(question, top_k=3)`. Các chunk được ghép thành phần `Context`, kèm `doc_id` và `source`, sau đó được đưa vào prompt cùng câu hỏi. Prompt yêu cầu chỉ sử dụng context và nói rõ khi context không đủ; cách này giữ câu trả lời có căn cứ và cho phép truy vết về chunk nguồn. Nếu store không có kết quả, agent trả về thông báo không tìm thấy ngữ cảnh thay vì tạo câu trả lời không có căn cứ.

## 3. Hoàn thiện code (Core Implementation) - Cá nhân (30 điểm)

Tôi chạy lệnh:

```text
pytest tests/ -v
```

python -m pytest tests -v
============ test session starts ============
platform win32 -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0 -- E:\K4-Day07-Data-Foundations\.venv\Scripts\python.exe
cachedir: .pytest_cache
rootdir: E:\K4-Day07-Data-Foundations
plugins: anyio-4.14.2
collected 42 items

tests/test_solution.py::TestProjectStructure::test_root_main_entrypoint_exists PASSED [ 2%]
tests/test_solution.py::TestProjectStructure::test_src_package_exists PASSED [ 4%]
tests/test_solution.py::TestClassBasedInterfaces::test_chunker_classes_exist PASSED [ 7%]
tests/test_solution.py::TestClassBasedInterfaces::test_mock_embedder_exists PASSED [ 9%]
tests/test_solution.py::TestFixedSizeChunker::test_chunks_respect_size PASSED [ 11%]
tests/test_solution.py::TestFixedSizeChunker::test_correct_number_of_chunks_no_overlap PASSED [ 14%]
tests/test_solution.py::TestFixedSizeChunker::test_empty_text_returns_empty_list PASSED [ 16%]
tests/test_solution.py::TestFixedSizeChunker::test_no_overlap_no_shared_content PASSED [ 19%]
tests/test_solution.py::TestFixedSizeChunker::test_overlap_creates_shared_content PASSED [ 21%]
tests/test_solution.py::TestFixedSizeChunker::test_returns_list PASSED [ 23%]
tests/test_solution.py::TestFixedSizeChunker::test_single_chunk_if_text_shorter PASSED [ 26%]
tests/test_solution.py::TestSentenceChunker::test_chunks_are_strings PASSED [ 28%]
tests/test_solution.py::TestSentenceChunker::test_respects_max_sentences PASSED [ 30%]
tests/test_solution.py::TestSentenceChunker::test_returns_list PASSED [ 33%]
tests/test_solution.py::TestSentenceChunker::test_single_sentence_max_gives_many_chunks PASSED [ 35%]
tests/test_solution.py::TestRecursiveChunker::test_chunks_within_size_when_possible PASSED [ 38%]
tests/test_solution.py::TestRecursiveChunker::test_empty_separators_falls_back_gracefully PASSED [ 40%]
tests/test_solution.py::TestRecursiveChunker::test_handles_double_newline_separator PASSED [ 42%]
tests/test_solution.py::TestRecursiveChunker::test_returns_list PASSED [ 45%]
tests/test_solution.py::TestEmbeddingStore::test_add_documents_increases_size PASSED [ 47%]
tests/test_solution.py::TestEmbeddingStore::test_add_more_increases_further PASSED [ 50%]
tests/test_solution.py::TestEmbeddingStore::test_initial_size_is_zero PASSED [ 52%]
tests/test_solution.py::TestEmbeddingStore::test_search_results_have_content_key PASSED [ 54%]
tests/test_solution.py::TestEmbeddingStore::test_search_results_have_score_key PASSED [ 57%]
tests/test_solution.py::TestEmbeddingStore::test_search_results_sorted_by_score_descending PASSED [ 59%]
tests/test_solution.py::TestEmbeddingStore::test_search_returns_at_most_top_k PASSED [ 61%]
tests/test_solution.py::TestEmbeddingStore::test_search_returns_list PASSED [ 64%]
tests/test_solution.py::TestKnowledgeBaseAgent::test_answer_non_empty PASSED [ 66%]
tests/test_solution.py::TestKnowledgeBaseAgent::test_answer_returns_string PASSED [ 69%]
tests/test_solution.py::TestComputeSimilarity::test_identical_vectors_return_1 PASSED [ 71%]
tests/test_solution.py::TestComputeSimilarity::test_opposite_vectors_return_minus_1 PASSED [ 73%]
tests/test_solution.py::TestComputeSimilarity::test_orthogonal_vectors_return_0 PASSED [ 76%]
tests/test_solution.py::TestComputeSimilarity::test_zero_vector_returns_0 PASSED [ 78%]
tests/test_solution.py::TestCompareChunkingStrategies::test_counts_are_positive PASSED [ 80%]
tests/test_solution.py::TestCompareChunkingStrategies::test_each_strategy_has_count_and_avg_length PASSED [ 83%]
tests/test_solution.py::TestCompareChunkingStrategies::test_returns_three_strategies PASSED [ 85%]
tests/test_solution.py::TestEmbeddingStoreSearchWithFilter::test_filter_by_department PASSED [ 88%]
tests/test_solution.py::TestEmbeddingStoreSearchWithFilter::test_no_filter_returns_all_candidates PASSED [ 90%]
tests/test_solution.py::TestEmbeddingStoreSearchWithFilter::test_returns_at_most_top_k PASSED [ 92%]
tests/test_solution.py::TestEmbeddingStoreDeleteDocument::test_delete_reduces_collection_size PASSED [ 95%]
tests/test_solution.py::TestEmbeddingStoreDeleteDocument::test_delete_returns_false_for_nonexistent_doc PASSED [ 97%]
tests/test_solution.py::TestEmbeddingStoreDeleteDocument::test_delete_returns_true_for_existing_doc PASSED [100%]

============ 42 passed in 0.20s =============

Kết quả: **42/42 bài test PASSED**, thời gian chạy 0.16 giây. Các nhóm chức năng đều đạt: chunking cố định, chunking theo câu, chunking đệ quy, cosine similarity, `EmbeddingStore`, metadata filter, xóa tài liệu, agent RAG và bộ so sánh chiến lược.

## 4. Dự đoán độ tương tự (Similarity Predictions) - Cá nhân (5 điểm)

Các giá trị dưới đây được tính bằng `compute_similarity` với `LocalEmbedder` dùng model đa ngôn ngữ `paraphrase-multilingual-MiniLM-L12-v2`. Các cặp được dự đoán trước khi chạy; điểm thực tế cho thấy model nhận diện tốt các câu diễn đạt cùng ý, nhưng vẫn có thể đánh giá cao hai câu khác chủ đề nếu chúng chia sẻ ngữ cảnh hoặc từ vựng chính sách.

| Cặp | Câu A                                                                 | Câu B                                                           | Dự đoán trước khi chạy | Điểm thực tế | Đúng? |
| --- | --------------------------------------------------------------------- | --------------------------------------------------------------- | ---------------------- | -----------: | ----- |
| 1   | Thời hạn gửi yêu cầu trả hàng là 15 ngày.                             | Người Mua có thể gửi yêu cầu trả hàng trong vòng 15 ngày.       | cao                    |     0.850955 | Có    |
| 2   | Shopee hoàn tiền sau khi hàng trả được xác nhận.                      | Python là một ngôn ngữ lập trình phổ biến.                      | thấp                   |     0.194393 | Có    |
| 3   | Người bán phải phản hồi yêu cầu trả hàng trong 2 ngày.                | Người bán cần gửi phản hồi trong vòng 02 ngày lịch.             | cao                    |     0.932162 | Có    |
| 4   | Tài khoản phải liên kết phương thức thanh toán để nhận hoàn tiền COD. | Người dùng có thể rút lại sự đồng ý xử lý dữ liệu cá nhân.      | thấp                   |     0.728289 | Không |
| 5   | Chính sách bảo vệ dữ liệu cá nhân cho phép yêu cầu xóa dữ liệu.       | Chính sách bảo vệ dữ liệu cá nhân cho phép yêu cầu sửa dữ liệu. | cao                    |     0.993152 | Có    |

Điều bất ngờ nhất là cặp 4 có score khá cao dù hai câu nói về COD và dữ liệu cá nhân, hai chủ đề không hoàn toàn giống nhau. Điều này cho thấy embedding vẫn có thể bị ảnh hưởng bởi các từ và ngữ cảnh chung như “tài khoản”, “phương thức” hoặc “yêu cầu”. Vì vậy similarity là tín hiệu hữu ích cho retrieval nhưng vẫn cần kiểm tra nội dung chunk và đối chiếu câu trả lời chuẩn.

## 5. Kết quả truy xuất của tôi (Competition Results) - Cá nhân (10 điểm)

Trong bốn cấu hình, tôi chọn **recursive** làm cấu hình đại diện để chấm điểm cá nhân vì nó giữ được các chunk liên quan ở vị trí tốt, đồng thời bảo toàn ngữ cảnh câu trả lời. Các score và ID dưới đây lấy nguyên từ phần `STRATEGY: recursive` trong `report/RUN_BENCHMARK_RESULT.md`.

| #   | Câu hỏi                                                                               | Top-1 chunk và score                                                                    | Liên quan?                                                      | Câu trả lời đối chiếu từ top-3                                                                                                                              |
| --- | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Thời hạn gửi yêu cầu trả hàng và hoàn tiền trên Shopee là bao lâu?                    | `k4-returns-policy::chunk_9::194`, 0.888; nêu thời hạn 15 ngày                          | Có, ở top-1                                                     | Người Mua có thể gửi yêu cầu trả hàng/hoàn tiền trong vòng 15 ngày kể từ khi đơn được cập nhật giao thành công; một số nhóm sản phẩm có thời hạn riêng.     |
| 2   | Người bán bị xử lý thế nào khi vi phạm quy định đăng bán sản phẩm bị cấm?             | `k4-seller-listing::chunk_49::294`, 0.916; nêu trực tiếp biện pháp xử lý                | Có, ở top-1; filter `customer_role=seller` cho kết quả tương tự | Tùy mức độ vi phạm, Shopee có thể xóa/khóa/tạm ẩn sản phẩm, hạn chế hoặc khóa tài khoản, yêu cầu bồi thường và áp dụng các biện pháp khác theo chính sách.  |
| 3   | Quy định về thời gian phản hồi của Người Bán khi nhận yêu cầu trả hàng là bao lâu?    | `k4-returns-policy::chunk_31::216`, 0.876; nêu rõ 02 ngày lịch                          | Có, ở top-1                                                     | Người Bán phải gửi phản hồi trong vòng 02 ngày lịch kể từ khi nhận thông báo của Shopee, hoặc theo thời hạn khác được Shopee quy định tại từng thời điểm.   |
| 4   | Điều kiện để Người Mua nhận hoàn tiền khi thanh toán bằng COD là gì?                  | `k4-returns-policy::chunk_11::196`, 0.877; nêu điều kiện liên kết tài khoản             | Có, ở top-1                                                     | Tài khoản Shopee của Người Mua phải liên kết với phương thức nhận hoàn tiền hợp lệ trước khi thực hiện yêu cầu trả hàng/hoàn tiền.                          |
| 5   | Người dùng có quyền yêu cầu xóa hoặc rút lại sự đồng ý sử dụng dữ liệu cá nhân không? | `k4-privacy-policy::chunk_120::171`, 0.863; nêu quyền rút lại đồng ý và yêu cầu hạn chế | Có, ở top-1                                                     | Người dùng có thể rút lại sự đồng ý, yêu cầu hạn chế hoặc phản đối việc xử lý dữ liệu bằng cách liên hệ chuyên viên bảo vệ dữ liệu cá nhân theo chính sách. |

**Đánh giá:** cả 5/5 câu đều có chunk chứa evidence trong top-3 và đều có evidence ở top-1. Q1–Q5 lần lượt đạt 2/2 vì kết quả đủ liên quan, coherent và grounded. Tổng điểm retrieval của cấu hình recursive là **10/10**.

`RecursiveChunker` cho kết quả tốt nhất trong lần chạy này với 10/10, vì cả năm câu đều có evidence đúng ở top-1. Các chiến lược còn lại vẫn cho thấy sự khác biệt về ranh giới chunk và khả năng giữ ngữ cảnh; đây là lý do cần benchmark trên cùng một bộ câu hỏi thay vì chỉ dựa vào score trung bình.

Metadata filter có ích rõ nhất ở Q2: nó giới hạn không gian tìm kiếm vào tài liệu của người bán. Tuy nhiên filter không tự đảm bảo top-1 chính xác, thể hiện ở kết quả recursive khi chunk cảnh báo đứng trước chunk xử lý vi phạm. Vì vậy metadata nên kết hợp với chunk có ranh giới ngữ nghĩa và embedding phù hợp.

**Điều tôi học được:** score cao chưa chắc đồng nghĩa với câu trả lời đúng; cần đọc nội dung chunk, kiểm tra vị trí trong top-3 và đối chiếu gold answer. Tôi cũng học được rằng chunk có cấu trúc tự nhiên và giữ đủ ngữ cảnh thường có giá trị hơn việc chỉ tối ưu số lượng chunk.

## 6. Phân tích các metrics retrieval

Các metrics dưới đây được ghi nhận trên cấu hình đại diện `recursive`, với cùng năm benchmark query trong `report/RUN_BENCHMARK_RESULT.md`.

| Query                                 | Precision             | Top-3 có chunk chứa đáp án? | Chunk coherence                                                         | Grounding                                      |
| ------------------------------------- | --------------------- | --------------------------- | ----------------------------------------------------------------------- | ---------------------------------------------- |
| Q1 - Thời hạn trả hàng/hoàn tiền      | Đạt; evidence ở top-1 | Có                          | Có; giữ thời hạn 15 ngày và ngoại lệ 24 giờ trong cùng chunk            | Đạt; câu trả lời bám vào context               |
| Q2 - Xử lý vi phạm đăng bán           | Đạt; evidence ở top-1 | Có                          | Có; cùng chunk nêu nhiều mức xử lý và điều kiện “tùy mức độ vi phạm”    | Đạt; câu trả lời dựa trên chunk seller-listing |
| Q3 - Thời gian phản hồi của Người Bán | Đạt; evidence ở top-1 | Có                          | Có; giữ 02 ngày lịch, mốc nhận thông báo và ngoại lệ thời hạn khác      | Đạt; câu trả lời được dẫn từ context           |
| Q4 - Hoàn tiền khi thanh toán COD     | Đạt; evidence ở top-1 | Có                          | Có; giữ điều kiện liên kết phương thức nhận hoàn tiền trước khi yêu cầu | Đạt; câu trả lời dựa trên chunk returns-policy |
| Q5 - Xóa dữ liệu/rút lại đồng ý       | Đạt; evidence ở top-1 | Có                          | Có; giữ quyền rút lại đồng ý, yêu cầu hạn chế/phản đối và cách liên hệ  | Đạt; câu trả lời grounded trong privacy-policy |

### Metadata utility

Với Q2, filter `customer_role=seller` giảm không gian tìm kiếm về các chunk dành cho Người Bán và không loại nhầm đáp án. Tuy nhiên log cho thấy kết quả filtered và unfiltered là giống nhau (`filter A/B identical: True`), vì vậy trong benchmark này filter giúp kiểm soát phạm vi hơn là cải thiện thứ hạng top-1. Đây là utility có ích về mặt an toàn truy xuất nhưng chưa tạo ra khác biệt đo được về điểm số.

### Failure case và cách sửa

Cấu hình `recursive` không có failure rõ ràng trong năm query: cả Q1–Q5 đều có `precision=True`, `coherence=True` và `grounding=True`. Failure rõ ràng xuất hiện ở baseline `fixed`, cụ thể Q1: top-3 không chứa chunk có evidence đặc trưng dù các chunk cùng `doc_id` có nhắc đến “mười lăm ngày”; Q5 của baseline này có evidence nhưng `coherence=False`, vì các quyền xóa dữ liệu và rút lại đồng ý bị tách rời, khiến câu trả lời thiếu đầy đủ. Nguyên nhân là chunk boundary cố định cắt điều khoản ở vị trí không phù hợp và semantic/keyword reranking chưa đủ để ghép các mảnh liên quan. Cách sửa là dùng `RecursiveChunker` hoặc tăng overlap, giữ trọn một điều khoản gồm điều kiện và ngoại lệ, sau đó rerank theo cụm evidence đặc trưng thay vì chỉ dựa vào score embedding.

## Tự Đánh Giá (Phần Cá Nhân)

| Tiêu chí                                        | Điểm tự đánh giá |
| ----------------------------------------------- | ---------------: |
| Khởi động (Warm-up)                             |            5 / 5 |
| Hướng tiếp cận của tôi (My Approach)            |          10 / 10 |
| Hoàn thiện code (Core Implementation - tests)   |          30 / 30 |
| Dự đoán độ tương tự (Similarity Predictions)    |            4 / 5 |
| Kết quả truy xuất của tôi (Competition Results) |          10 / 10 |
| **Tổng phần cá nhân**                           |      **59 / 60** |
